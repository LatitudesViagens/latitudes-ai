"""Apoio aos testes: fixtures, transporte fictício e trava contra internet."""

import json
from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures"

TOKEN_FICTICIO = "TOKEN-FICTICIO-RD-0000"
CHAVE_FICTICIA = "CHAVE-FICTICIA-ENVISION-0000"
BASE_ENVISION_FICTICIA = "https://envision.exemplo.invalid"


def fixture(caminho: str):
    return json.loads((FIXTURES / caminho).read_text(encoding="utf-8"))


class TransporteFicticio(httpx.MockTransport):
    """Responde com fixtures e guarda as requisições recebidas.

    `rotas`: {(método, caminho): resposta}, onde resposta é um dict/list
    (status 200), um httpx.Response, uma exceção ou uma lista desses (uma por
    chamada, na ordem).
    """

    def __init__(self, rotas: dict) -> None:
        self.rotas = {chave: (valor if isinstance(valor, list) and valor and isinstance(valor[0], (httpx.Response, Exception)) else [valor]) for chave, valor in rotas.items()}
        self.requisicoes: list[httpx.Request] = []
        super().__init__(self._responder)

    def _responder(self, requisicao: httpx.Request) -> httpx.Response:
        self.requisicoes.append(requisicao)
        caminho = requisicao.url.path

        for prefixo in ("/api/v1",):
            if caminho.startswith(prefixo):
                caminho = caminho[len(prefixo):]

        respostas = self.rotas.get((requisicao.method, caminho))

        if not respostas:
            return httpx.Response(404, json={"erro": "rota fictícia não encontrada"})

        resposta = respostas.pop(0) if len(respostas) > 1 else respostas[0]

        if isinstance(resposta, Exception):
            raise resposta

        if isinstance(resposta, httpx.Response):
            return resposta

        return httpx.Response(200, json=resposta)


CREDENCIAIS_REAIS = (
    "RD_CRM_API_TOKEN",
    "ENVISION_BASE_URL",
    "ENVISION_API_KEY",
    "ENVISION_USUARIO",
    "ENVISION_SENHA",
    "ENVISION_AUTH_FORMATO",
    "ENVISION_USERNAME",
    "ENVISION_PASSWORD",
    "ENVISION_TRAVEL_AGENCY_ID",
    "ENVISION_TRAVEL_AGENCY_SYSTEM_ACCOUNT_ID",
    "ENVISION_SYSTEM_ACCOUNT_ID",
    "ENVISION_CONSOLIDATOR_ID",
    "ENVISION_CONSOLIDATOR_SYSTEM_ACCOUNT_ID",
)


def bloquear_internet() -> None:
    """Qualquer requisição que não passe por um TransporteFicticio falha, e as
    credenciais reais do .env ficam invisíveis para os testes."""
    import os

    import integracoes.config  # noqa: F401 (carrega o .env antes de limpar)

    # Vazias (e não apagadas): o load_dotenv de outros módulos não sobrescreve
    # variáveis que já existem.
    for nome in CREDENCIAIS_REAIS:
        os.environ[nome] = ""


    def recusar(self, request):
        raise AssertionError(
            f"Teste tentou acessar a internet: {request.method} {request.url.host}"
        )

    httpx.HTTPTransport.handle_request = recusar
