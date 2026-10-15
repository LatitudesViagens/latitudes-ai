from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import threading

import httpx

from dotenv import load_dotenv
from supabase import Client, create_client
from supabase.client import ClientOptions


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "latitudes_agent" / ".env"

load_dotenv(dotenv_path=ENV_FILE)


def _credentials() -> tuple[str, str]:
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        raise RuntimeError(
            "As variáveis SUPABASE_URL e SUPABASE_KEY não foram configuradas."
        )

    return supabase_url, supabase_key


# Por padrão a conexão com o banco fecha depois de 5 s parada, e a seguinte
# precisa abrir uma conexão segura nova (medido em 13/10/2026: 0,2 a 3 s,
# por exemplo ao gravar a resposta depois de 8–18 s de IA, ou a cada clique
# vindo mais de 5 s depois do anterior). 50 s fica abaixo do tempo em que os
# servidores costumam derrubar conexões paradas.
CONNECTION_KEEPALIVE_SECONDS = 50


def keep_connection_alive(client: Client) -> None:
    """Mantém a conexão com o banco aberta por mais tempo. O Supabase recria a
    conexão no login e ao renovar o token, então a tela chama a cada execução
    (só muda um número). Falha aqui nunca derruba nada."""
    try:
        transport = client.postgrest.session._transport

        if isinstance(transport, _RetryStaleConnection):
            transport = transport.inner

        transport._pool._keepalive_expiry = CONNECTION_KEEPALIVE_SECONDS
    except AttributeError:
        pass


class _RetryStaleConnection(httpx.BaseTransport):
    """Repete UMA vez uma consulta que caiu numa conexão parada derrubada pelo
    servidor. Só para métodos que podem ser repetidos sem efeito duplicado
    (ler, atualizar, apagar); inserções (POST) nunca são repetidas."""

    REPEATABLE = {"GET", "HEAD", "OPTIONS", "PATCH", "PUT", "DELETE"}
    STALE_ERRORS = (
        httpx.RemoteProtocolError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.ConnectError,
    )

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self.inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return self.inner.handle_request(request)
        except self.STALE_ERRORS as error:
            if request.method not in self.REPEATABLE:
                raise

            print(
                f"[BANCO] Conexão parada caiu ({type(error).__name__}); "
                "repetindo com uma nova.",
                flush=True,
            )
            return self.inner.handle_request(request)

    def close(self) -> None:
        self.inner.close()


def get_supabase_client() -> Client:
    supabase_url, supabase_key = _credentials()
    return create_client(supabase_url, supabase_key)


def get_access_token(client: Client) -> str:
    """Token de acesso da sessão. Chame na thread da tela, dona da sessão."""
    session = client.auth.get_session()

    if session is None:
        raise RuntimeError("Sessão não encontrada.")

    return session.access_token


def get_background_client(access_token: str) -> Client:
    """Conexão própria para uma tarefa em segundo plano, com o mesmo login.

    As bibliotecas do Supabase usam HTTP/2, que não é seguro quando a mesma
    conexão é usada por várias threads ao mesmo tempo (causava travadas e
    falhas ao carregar conversas). Esta conexão nunca renova o token: o
    Supabase invalida o token anterior a cada renovação, o que deslogaria a
    pessoa.
    """
    supabase_url, supabase_key = _credentials()

    client = create_client(
        supabase_url,
        supabase_key,
        options=ClientOptions(
            headers={"Authorization": f"Bearer {access_token}"},
            auto_refresh_token=False,
            persist_session=False,
        ),
    )
    # A mesma conexão serve do começo ao fim da resposta, inclusive para
    # gravá-la depois da espera da IA.
    keep_connection_alive(client)

    try:
        session = client.postgrest.session
        session._transport = _RetryStaleConnection(session._transport)
    except AttributeError:
        pass

    return client


# Conexões das tarefas guardadas entre respostas, pela chave do LOGIN (o
# token): uma conexão nunca é usada para outra pessoa, nem por duas tarefas ao
# mesmo tempo (o cliente usa HTTP/2, que trava se compartilhado entre threads).
# Abrir uma conexão nova custava ~0,9 s por resposta (medido em 13/10/2026).
_idle_lock = threading.Lock()
_idle_clients: "OrderedDict[str, list[Client]]" = OrderedDict()
MAX_IDLE_PER_LOGIN = 2
MAX_LOGINS_KEPT = 100


@contextmanager
def background_client(access_token: str) -> Iterator[Client]:
    """Empresta uma conexão da tarefa (guardada ou nova) e a devolve no fim.
    Um login renovado (token novo) recebe uma conexão nova; as antigas saem
    da lista quando passam do limite."""
    with _idle_lock:
        idle = _idle_clients.get(access_token)
        client = idle.pop() if idle else None

    if client is None:
        client = get_background_client(access_token)

    try:
        yield client
    finally:
        with _idle_lock:
            idle = _idle_clients.setdefault(access_token, [])
            _idle_clients.move_to_end(access_token)

            if len(idle) < MAX_IDLE_PER_LOGIN:
                idle.append(client)

            while len(_idle_clients) > MAX_LOGINS_KEPT:
                _idle_clients.popitem(last=False)