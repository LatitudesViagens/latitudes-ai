import os
from pathlib import Path

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

    return create_client(
        supabase_url,
        supabase_key,
        options=ClientOptions(
            headers={"Authorization": f"Bearer {access_token}"},
            auto_refresh_token=False,
            persist_session=False,
        ),
    )