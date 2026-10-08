"""Requisições somente leitura, com duas travas.

1. Cada conector informa sua lista fechada de (método, caminho) permitidos.
   Qualquer outra combinação é bloqueada antes de sair da máquina.
2. Esta camada ainda recusa, para qualquer conector: métodos que não sejam GET
   (exceto as consultas POST listadas em CONSULTAS_POST) e qualquer caminho de
   /Records fora de LEITURAS_RECORDS — mesmo que um conector peça.

Erros viram IntegracaoError com mensagem fixa: nunca levam URL (o token do RD
vai nela), cabeçalhos ou o corpo da resposta.
"""

from collections.abc import Callable
from dataclasses import dataclass
import re
import time

import httpx

from integracoes.erros import ChamadaBloqueadaError, IntegracaoError

TIMEOUT_SEGUNDOS = 10
ESPERA_MAXIMA_429_SEGUNDOS = 5

# Consultas que a API do Envision só oferece por POST (decisão de 07/10/2026).
CONSULTAS_POST = frozenset(
    {
        ("Envision", "/Records/Query"),
        # Busca de viajantes por nome, usada pelo programador do formulário
        # (08/10/2026). Fora do Swagger; liberada só para leitura/teste.
        ("Envision", "/Shopping/QueryTravellers"),
    }
)

# Login (OAuth2) — POST que só devolve um token temporário, sem gravar nada.
AUTENTICACAO_POST = frozenset(
    {
        ("Envision", "/token"),
    }
)

# Abertura de sessão no contexto da empresa (agência/conta) — POST que não
# grava dados; o programador do formulário de viajantes usa antes das
# consultas (08/10/2026). Os outros /Authorization (trocar/resetar senha)
# continuam bloqueados.
SESSAO_POST = frozenset(
    {
        ("Envision", "/Authorization/GetSession"),
    }
)

# Únicas leituras liberadas dentro de /Records (o resto é escrita).
LEITURAS_RECORDS = (
    ("POST", re.compile(r"^/Records/Query$")),
    ("GET", re.compile(r"^/Records/\d+$")),
    ("GET", re.compile(r"^/Records/GetServiceOrderSummaries$")),
)


@dataclass(frozen=True)
class EndpointPermitido:
    metodo: str
    caminho: re.Pattern


def endpoint(
    metodo: str,
    padrao: str,
) -> EndpointPermitido:
    return EndpointPermitido(
        metodo=metodo.upper(),
        caminho=re.compile(padrao),
    )


def verificar_somente_leitura(
    sistema: str,
    metodo: str,
    caminho: str,
) -> None:
    """Trava global: levanta ChamadaBloqueadaError para qualquer escrita."""
    metodo = metodo.upper()

    if metodo == "POST":
        if (sistema, caminho) not in CONSULTAS_POST:
            raise ChamadaBloqueadaError(sistema, metodo, caminho)
    elif metodo != "GET":
        raise ChamadaBloqueadaError(sistema, metodo, caminho)

    if caminho.lower().startswith("/records") and not any(
        metodo == metodo_permitido and padrao.match(caminho)
        for metodo_permitido, padrao in LEITURAS_RECORDS
    ):
        raise ChamadaBloqueadaError(sistema, metodo, caminho)


class ClienteLeitura:
    """Cliente HTTP de um sistema, limitado à lista de endpoints permitidos.

    `autenticar` recebe (cabeçalhos, parâmetros) e acrescenta a credencial.
    `transporte` permite usar respostas fictícias nos testes
    (httpx.MockTransport); em produção fica None.
    """

    def __init__(
        self,
        sistema: str,
        base_url: str,
        permitidos: tuple[EndpointPermitido, ...],
        autenticar: Callable[[dict, dict], None],
        transporte: httpx.BaseTransport | None = None,
        esperar: Callable[[float], None] = time.sleep,
        timeout: float = TIMEOUT_SEGUNDOS,
    ) -> None:
        self.sistema = sistema
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")
        self._permitidos = permitidos
        self._autenticar = autenticar
        self._transporte = transporte
        self._esperar = esperar

    def _verificar(
        self,
        metodo: str,
        caminho: str,
    ) -> None:
        verificar_somente_leitura(
            sistema=self.sistema,
            metodo=metodo,
            caminho=caminho,
        )

        if not any(
            permitido.metodo == metodo and permitido.caminho.match(caminho)
            for permitido in self._permitidos
        ):
            raise ChamadaBloqueadaError(self.sistema, metodo, caminho)

    def get(
        self,
        caminho: str,
        params: dict | None = None,
    ):
        return self._requisitar(
            metodo="GET",
            caminho=caminho,
            params=params,
        )

    def consultar_post(
        self,
        caminho: str,
        corpo: dict,
    ):
        """POST apenas para consultas listadas em CONSULTAS_POST."""
        return self._requisitar(
            metodo="POST",
            caminho=caminho,
            corpo=corpo,
        )

    def iniciar_sessao(
        self,
        caminho: str,
        corpo: dict,
    ):
        """POST de abertura de sessão (só os caminhos de SESSAO_POST)."""
        if (self.sistema, caminho) not in SESSAO_POST:
            raise ChamadaBloqueadaError(self.sistema, "POST", caminho)

        return self._requisitar(
            metodo="POST",
            caminho=caminho,
            corpo=corpo,
            sessao=True,
        )

    def obter_token(
        self,
        caminho: str,
        formulario: dict,
    ) -> dict:
        """Login OAuth2 (só os caminhos de AUTENTICACAO_POST). O formulário
        e a resposta (token) nunca aparecem em erros."""
        if (self.sistema, caminho) not in AUTENTICACAO_POST:
            raise ChamadaBloqueadaError(self.sistema, "POST", caminho)

        falha: str | None = None

        try:
            with httpx.Client(
                timeout=self._timeout,
                transport=self._transporte,
            ) as cliente:
                resposta = cliente.post(
                    self._base_url + caminho,
                    data=formulario,
                    headers={"Accept": "application/json"},
                )
        except httpx.TimeoutException:
            falha = "timeout"
        except httpx.HTTPError:
            falha = "conexao"

        if falha:
            raise IntegracaoError(self.sistema, falha)

        # Usuário, senha ou client_id recusados costumam vir como 400.
        if resposta.status_code in {400, 401}:
            raise IntegracaoError(self.sistema, "credencial", resposta.status_code)

        dados = self._ler_resposta(resposta)

        if not isinstance(dados, dict) or not dados.get("access_token"):
            raise IntegracaoError(self.sistema, "resposta")

        return dados

    def _requisitar(
        self,
        metodo: str,
        caminho: str,
        params: dict | None = None,
        corpo: dict | None = None,
        sessao: bool = False,
    ):
        # A abertura de sessão já foi conferida em iniciar_sessao (SESSAO_POST).
        if not sessao:
            self._verificar(metodo, caminho)

        headers = {"Accept": "application/json"}
        query = dict(params or {})
        self._autenticar(headers, query)

        for tentativa in range(2):
            falha: str | None = None

            try:
                with httpx.Client(
                    timeout=self._timeout,
                    transport=self._transporte,
                ) as cliente:
                    resposta = cliente.request(
                        metodo,
                        self._base_url + caminho,
                        params=query,
                        json=corpo,
                        headers=headers,
                    )
            except httpx.TimeoutException:
                falha = "timeout"
            except httpx.HTTPError:
                falha = "conexao"

            # Levantado fora do except: o erro não carrega a exceção do httpx,
            # que pode trazer a URL (com o token do RD) no texto.
            if falha:
                raise IntegracaoError(self.sistema, falha)

            if resposta.status_code == 429 and tentativa == 0:
                self._esperar(self._espera_429(resposta))
                continue

            break

        return self._ler_resposta(resposta)

    @staticmethod
    def _espera_429(resposta: httpx.Response) -> float:
        try:
            segundos = float(resposta.headers.get("Retry-After", "2"))
        except ValueError:
            segundos = 2.0

        return min(max(segundos, 0.0), ESPERA_MAXIMA_429_SEGUNDOS)

    def _ler_resposta(self, resposta: httpx.Response):
        status = resposta.status_code
        tipos = {
            401: "credencial",
            403: "permissao",
            404: "nao_encontrado",
            429: "limite",
        }

        if status in tipos:
            raise IntegracaoError(self.sistema, tipos[status], status)

        if status >= 500:
            raise IntegracaoError(self.sistema, "servidor", status)

        if status >= 400:
            raise IntegracaoError(self.sistema, "resposta", status)

        try:
            return resposta.json()
        except ValueError:
            raise IntegracaoError(self.sistema, "resposta", status) from None
