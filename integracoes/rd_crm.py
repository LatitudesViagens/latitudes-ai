"""RD Station CRM (API v1), somente leitura.

Documentação oficial: https://developers.rdstation.com (CRM v1). O token vai
no parâmetro `token` da URL, por isso nenhuma URL é impressa (erros.py).
"""

import httpx

from integracoes import config
from integracoes.erros import IntegracaoError
from integracoes.http import ClienteLeitura, endpoint

SISTEMA = "RD Station CRM"
LIMITE_POR_PAGINA = 200
MAXIMO_PAGINAS = 5

ENDPOINTS_PERMITIDOS = (
    endpoint("GET", r"^/contacts$"),
    endpoint("GET", r"^/contacts/[A-Za-z0-9]+$"),
    endpoint("GET", r"^/deals/[A-Za-z0-9]+$"),
    endpoint("GET", r"^/deal_pipelines$"),
    endpoint("GET", r"^/custom_fields$"),
)


class RDCRM:
    nome = SISTEMA

    def __init__(
        self,
        token: str | None = None,
        transporte: httpx.BaseTransport | None = None,
        esperar=None,
    ) -> None:
        token = token if token is not None else config.rd_crm_token()

        if not token:
            raise IntegracaoError(SISTEMA, "credencial_ausente")

        def autenticar(headers: dict, params: dict) -> None:
            params["token"] = token

        extras = {"esperar": esperar} if esperar else {}
        self._http = ClienteLeitura(
            sistema=SISTEMA,
            base_url=config.RD_CRM_BASE_URL,
            permitidos=ENDPOINTS_PERMITIDOS,
            autenticar=autenticar,
            transporte=transporte,
            **extras,
        )

    def testar_conexao(self) -> None:
        self._http.get(
            "/deal_pipelines",
            params={"limit": 1},
        )

    def buscar_contatos(
        self,
        nome: str | None = None,
        email: str | None = None,
    ) -> list[dict]:
        """Contatos por nome (busca parcial do RD) ou e-mail."""
        params: dict = {"limit": LIMITE_POR_PAGINA}

        if nome:
            params["q"] = nome

        if email:
            params["email"] = email

        contatos: list[dict] = []

        for pagina in range(1, MAXIMO_PAGINAS + 1):
            dados = self._http.get(
                "/contacts",
                params={**params, "page": pagina},
            )
            contatos.extend(dados.get("contacts") or [])

            if not dados.get("has_more"):
                break

        return contatos

    def amostra_contatos(self, limite: int = 3) -> list[dict]:
        """Poucos contatos, para o script de verificação ver o formato."""
        dados = self._http.get(
            "/contacts",
            params={"limit": max(1, min(int(limite), 20)), "page": 1},
        )
        return dados.get("contacts") or []

    def listar_campos_personalizados(self, entidade: str) -> list[dict]:
        """Nomes e tipos dos campos personalizados ('contact' ou 'deal')."""
        if entidade not in {"contact", "deal", "organization"}:
            raise ValueError("Entidade inválida.")

        dados = self._http.get(
            "/custom_fields",
            params={"for": entidade},
        )
        return dados if isinstance(dados, list) else dados.get("custom_fields") or []

    def obter_contato(self, contato_id: str) -> dict:
        return self._http.get(f"/contacts/{contato_id}")

    def obter_negociacao(self, negociacao_id: str) -> dict:
        return self._http.get(f"/deals/{negociacao_id}")

    def listar_funis(self) -> list[dict]:
        funis: list[dict] = []

        for pagina in range(1, MAXIMO_PAGINAS + 1):
            dados = self._http.get(
                "/deal_pipelines",
                params={"limit": LIMITE_POR_PAGINA, "page": pagina},
            )
            lote = dados if isinstance(dados, list) else dados.get("deal_pipelines") or []
            funis.extend(lote)

            if len(lote) < LIMITE_POR_PAGINA:
                break

        return funis
