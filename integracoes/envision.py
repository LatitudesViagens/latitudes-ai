"""Envision (EnvisionAPI v1, Swagger em {ENVISION_BASE_URL}/swagger/docs/v1),
somente leitura.

As ordens de serviço ficam em /Records. Liberadas SÓ as consultas
(decisão de 07/10/2026): POST /Records/Query, GET /Records/{id} e
GET /Records/GetServiceOrderSummaries. Criar, alterar, pagar, mudar status
etc. são bloqueados aqui e na trava global (integracoes/http.py).

A chave vai no cabeçalho Authorization ("pura" ou "Bearer <chave>",
ENVISION_AUTH_FORMATO). A mesma chave tem permissão de escrita: por isso as
travas.
"""

import re
import time

import httpx

from integracoes import config
from integracoes.erros import IntegracaoError
from integracoes.http import ClienteLeitura, endpoint

SISTEMA = "Envision"
MAXIMO_PAGINAS = 10
# Cada venda é uma leitura (GET /Records/{id}); limite por cliente.
MAXIMO_VENDAS_POR_CPF = 60
# O /Records/Query pode passar de 10 s (sugestão do programador, 08/10/2026).
TIMEOUT_ENVISION_SEGUNDOS = 30

_CPF_TEXTO = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_EMAIL_TEXTO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _sem_dados_pessoais(texto: str) -> str:
    return _EMAIL_TEXTO.sub("***@***", _CPF_TEXTO.sub("***********", texto))

ENDPOINTS_PERMITIDOS = (
    endpoint("POST", r"^/Records/Query$"),
    endpoint("GET", r"^/Records/\d+$"),
    endpoint("GET", r"^/Records/GetServiceOrderSummaries$"),
    endpoint("POST", r"^/Shopping/QueryTravellers$"),
)


class Envision:
    nome = SISTEMA

    def __init__(
        self,
        base_url: str | None = None,
        chave: str | None = None,
        formato_chave: str | None = None,
        usuario: str | None = None,
        senha: str | None = None,
        transporte: httpx.BaseTransport | None = None,
        esperar=None,
        contexto: dict | None = None,
    ) -> None:
        base_url = base_url if base_url is not None else config.envision_base_url()
        chave = chave if chave is not None else config.envision_api_key()
        usuario = usuario if usuario is not None else config.envision_usuario()
        senha = senha if senha is not None else config.envision_senha()
        formato = formato_chave or config.envision_formato_chave()
        por_senha = formato.startswith("senha")

        if not base_url or (por_senha and not (usuario and senha)) or (not por_senha and not chave):
            raise IntegracaoError(SISTEMA, "credencial_ausente")

        if formato == "senha_com_chave" and not chave:
            raise IntegracaoError(SISTEMA, "credencial_ausente")

        if not base_url.startswith("https://"):
            # Credenciais nunca trafegam sem criptografia.
            raise IntegracaoError(SISTEMA, "conexao")

        self._token: str | None = None
        self._token_expira = 0.0
        self._contexto = config.envision_contexto() if contexto is None else (contexto or None)

        if por_senha:
            formulario = {
                "grant_type": "password",
                "username": usuario,
                "password": senha,
            }

            if formato == "senha_com_chave":
                formulario["client_id"] = chave

            def autenticar(headers: dict, params: dict) -> None:
                headers["Authorization"] = f"Bearer {self._token_valido(formulario)}"
        else:
            valor = f"Bearer {chave}" if formato == "bearer" else chave

            def autenticar(headers: dict, params: dict) -> None:
                headers["Authorization"] = valor

        extras = {"esperar": esperar} if esperar else {}
        self._http = ClienteLeitura(
            sistema=SISTEMA,
            base_url=base_url,
            permitidos=ENDPOINTS_PERMITIDOS,
            autenticar=autenticar,
            transporte=transporte,
            timeout=TIMEOUT_ENVISION_SEGUNDOS,
            **extras,
        )

    def _token_valido(self, formulario: dict) -> str:
        """Token OAuth2 em memória, renovado um minuto antes de vencer."""
        if self._token and time.monotonic() < self._token_expira:
            return self._token

        dados = self._http.obter_token("/token", formulario)
        self._token = str(dados["access_token"])
        validade = dados.get("expires_in")
        segundos = float(validade) if isinstance(validade, (int, float, str)) and str(validade).isdigit() else 600.0
        self._token_expira = time.monotonic() + max(segundos - 60, 30)
        self._abrir_sessao()
        return self._token

    def _abrir_sessao(self) -> None:
        """Abre a sessão no contexto da agência/conta (GetSession), como o
        formulário de viajantes faz antes de consultar. Só com o contexto
        configurado (ENVISION_TRAVEL_AGENCY_ID / ENVISION_SYSTEM_ACCOUNT_ID)."""
        if not self._contexto:
            return

        empresa = {
            "id": self._contexto["travelAgencyId"],
            "systemAccountId": self._contexto["systemAccountId"],
        }
        self._verificar_envelope(
            self._http.iniciar_sessao(
                "/Authorization/GetSession",
                corpo={
                    "companyContext": {
                        "consolidator": empresa,
                        "travelAgency": empresa,
                    }
                },
            )
        )

    def testar_conexao(self) -> None:
        self.resumo_ordens()

    def _verificar_envelope(self, dados) -> dict:
        """As respostas do Envision trazem `successful` e `errors`."""
        if not isinstance(dados, dict):
            raise IntegracaoError(SISTEMA, "resposta")

        if dados.get("successful") is False:
            motivos = [
                f"{erro.get('code', '')}: {erro.get('message', '')}".strip(": ")
                for erro in (dados.get("errors") or [])
                if isinstance(erro, dict)
            ]
            print(
                "[ENVISION] Consulta recusada: "
                + _sem_dados_pessoais("; ".join(motivos) or "sem detalhe"),
                flush=True,
            )
            raise IntegracaoError(SISTEMA, "resposta")

        return dados

    def consultar_registros(
        self,
        criteria: str,
        campos_ordenacao: list[str] | None = None,
        maximo_paginas: int = MAXIMO_PAGINAS,
        info_adicional: dict | None = None,
    ) -> list[dict]:
        """Registros (ordens de serviço etc.) que atendem ao `criteria`,
        percorrendo as páginas pelo pivô da própria API. Cada chamada pode ter
        custo para a Latitudes (aviso do Envision): use filtros estreitos."""
        # Formato do programador do formulário de viajantes (08/10/2026):
        # campo em minúsculas, valor entre aspas duplas e additionalInfo com
        # travelAgencyId e systemAccountId.
        corpo: dict = {
            "criteria": criteria,
            "sortFields": campos_ordenacao or [],
            "pagingPivotId": 0,
            "pagingPivotValues": [],
        }

        info = info_adicional if info_adicional is not None else config.envision_contexto()

        if info:
            # No Swagger, additionalInfo é um dicionário de TEXTO
            # ({"travelAgencyId": "160779"}); número pode ser ignorado.
            corpo["additionalInfo"] = {str(k): str(v) for k, v in info.items()}

        registros: list[dict] = []

        for _ in range(max(1, min(maximo_paginas, MAXIMO_PAGINAS))):
            dados = self._verificar_envelope(
                self._http.consultar_post(
                    "/Records/Query",
                    corpo=corpo,
                )
            )
            lote = dados.get("records") or []
            registros.extend(lote)
            pivo_id = dados.get("pagingPivotId")

            if not lote or not pivo_id or pivo_id == corpo.get("pagingPivotId"):
                break

            corpo = {
                **corpo,
                "pagingPivotId": pivo_id,
                "pagingPivotValues": dados.get("pagingPivotValues") or [],
            }

        return registros

    def obter_registro(self, registro_id: int) -> dict:
        return self._http.get(f"/Records/{int(registro_id)}")

    def vendas_por_cpf(
        self,
        cpf: str,
        maximo_vendas: int = MAXIMO_VENDAS_POR_CPF,
    ) -> tuple[bool, list[dict]]:
        """Vendas (ServiceOrder) ligadas ao cadastro Person do cliente, achado
        pelo CPF. Só tem cadastro quem preencheu o formulário de viajantes.
        Devolve (tem_cadastro, vendas)."""
        digitos = re.sub(r"\D", "", str(cpf or ""))

        if len(digitos) != 11:
            return False, []

        # Campo com inicial maiúscula: "externalId" é ignorado pela API
        # (nível 9 do script, 08/10/2026).
        cadastros = [
            registro
            for registro in self.consultar_registros(
                f'ExternalId = "{digitos}"',
                maximo_paginas=1,
            )
            # Confere o CPF de volta: se o filtro fosse ignorado, viriam
            # registros de outras pessoas.
            if re.sub(r"\D", "", str(registro.get("externalId") or "")) == digitos
        ]

        if not cadastros:
            return False, []

        ids: list[int] = []

        for cadastro in cadastros:
            for associacao in cadastro.get("associations") or []:
                registro_id = associacao.get("recordAssociatedId")

                if str(associacao.get("recordType")) == "ServiceOrder" and registro_id and int(registro_id) not in ids:
                    ids.append(int(registro_id))

        return True, [self.obter_registro(registro_id) for registro_id in ids[:maximo_vendas]]

    def buscar_viajantes(self, nome: str) -> list[dict]:
        """Viajantes com esse nome (POST /Shopping/QueryTravellers, leitura),
        no formato usado pelo programador do formulário."""
        corpo: dict = {"travellerName": str(nome or "").strip()}

        if self._contexto:
            empresa = {
                "id": int(self._contexto["travelAgencyId"]),
                "systemAccountId": int(self._contexto["systemAccountId"]),
            }
            corpo["companyContext"] = {"consolidator": empresa, "travelAgency": empresa}

        dados = self._verificar_envelope(
            self._http.consultar_post("/Shopping/QueryTravellers", corpo=corpo)
        )
        return dados.get("travellers") or []

    def resumo_ordens(self) -> list[dict]:
        dados = self._verificar_envelope(
            self._http.get("/Records/GetServiceOrderSummaries")
        )
        return dados.get("items") or []
