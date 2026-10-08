"""Camada 1: conectores somente leitura, com respostas fictícias."""

import io
import json
import logging
import unittest
from contextlib import redirect_stderr, redirect_stdout

import httpx

from integracoes.envision import Envision
from integracoes.erros import ChamadaBloqueadaError, IntegracaoError
from integracoes.http import verificar_somente_leitura
from integracoes.rd_crm import RDCRM
from tests.apoio import (
    BASE_ENVISION_FICTICIA,
    CHAVE_FICTICIA,
    TOKEN_FICTICIO,
    TransporteFicticio,
    bloquear_internet,
    fixture,
)

bloquear_internet()


def rd(rotas: dict, esperas: list | None = None) -> tuple[RDCRM, TransporteFicticio]:
    transporte = TransporteFicticio(rotas)
    conector = RDCRM(
        token=TOKEN_FICTICIO,
        transporte=transporte,
        esperar=(esperas.append if esperas is not None else (lambda s: None)),
    )
    return conector, transporte


def envision(rotas: dict, formato: str = "pura") -> tuple[Envision, TransporteFicticio]:
    transporte = TransporteFicticio(rotas)
    conector = Envision(
        base_url=BASE_ENVISION_FICTICIA,
        chave=CHAVE_FICTICIA,
        formato_chave=formato,
        transporte=transporte,
        esperar=lambda s: None,
    )
    return conector, transporte


class TestRDCRM(unittest.TestCase):
    def test_busca_contato_por_nome_envia_token_e_busca(self):
        conector, transporte = rd({("GET", "/contacts"): fixture("rd_crm/contacts_joao.json")})
        contatos = conector.buscar_contatos(nome="João")
        self.assertEqual([c["id"] for c in contatos], ["c1"])
        params = transporte.requisicoes[0].url.params
        self.assertEqual(params["q"], "João")
        self.assertEqual(params["token"], TOKEN_FICTICIO)
        self.assertEqual(transporte.requisicoes[0].method, "GET")

    def test_negociacao_e_funis(self):
        conector, _ = rd({
            ("GET", "/deals/d1"): fixture("rd_crm/deal_d1.json"),
            ("GET", "/deal_pipelines"): fixture("rd_crm/deal_pipelines.json"),
        })
        self.assertEqual(conector.obter_negociacao("d1")["deal_pipeline"]["name"], "Vendas | Grupo")
        self.assertEqual(len(conector.listar_funis()), 3)

    def test_id_com_barra_e_bloqueado(self):
        conector, transporte = rd({})
        with self.assertRaises(ChamadaBloqueadaError):
            conector.obter_negociacao("d1/../../contacts")
        self.assertEqual(transporte.requisicoes, [])

    def test_sem_token_falha_sem_mostrar_nada(self):
        with self.assertRaises(IntegracaoError) as erro:
            RDCRM(token="")
        self.assertEqual(erro.exception.tipo, "credencial_ausente")


class TestEnvision(unittest.TestCase):
    def test_consulta_por_post_so_em_records_query(self):
        conector, transporte = envision({("POST", "/Records/Query"): fixture("envision/query_records.json")})
        registros = conector.consultar_registros("criterio-ficticio")
        self.assertEqual(registros[0]["id"], 123)
        self.assertEqual(transporte.requisicoes[0].headers["Authorization"], CHAVE_FICTICIA)

    def test_formato_bearer(self):
        conector, transporte = envision({("GET", "/Records/GetServiceOrderSummaries"): fixture("envision/summaries.json")}, formato="bearer")
        conector.testar_conexao()
        self.assertEqual(transporte.requisicoes[0].headers["Authorization"], f"Bearer {CHAVE_FICTICIA}")

    def test_registro_e_resumo(self):
        conector, _ = envision({
            ("GET", "/Records/123"): fixture("envision/record_123.json"),
            ("GET", "/Records/GetServiceOrderSummaries"): fixture("envision/summaries.json"),
        })
        self.assertEqual(conector.obter_registro(123)["status"]["name"], "Emitido")
        self.assertEqual(len(conector.resumo_ordens()), 1)

    def test_envelope_sem_sucesso_vira_erro(self):
        conector, _ = envision({("POST", "/Records/Query"): fixture("envision/query_falhou.json")})
        with self.assertRaises(IntegracaoError) as erro:
            conector.consultar_registros("x")
        self.assertEqual(erro.exception.tipo, "resposta")

    def test_sem_https_recusa(self):
        with self.assertRaises(IntegracaoError):
            Envision(base_url="http://envision.exemplo.invalid", chave=CHAVE_FICTICIA)


class TestSomenteLeitura(unittest.TestCase):
    """Trava global: escrita bloqueada mesmo que alguém tente."""

    ESCRITAS_ENVISION = [
        ("POST", "/Records"),
        ("PATCH", "/Records/123"),
        ("POST", "/Records/Pay"),
        ("POST", "/Records/ProcessPayments"),
        ("POST", "/Records/ChangeStatus"),
        ("POST", "/Records/SaveMessage"),
        ("POST", "/Records/ExecuteActionRequired"),
        ("GET", "/Records/History/123"),
        ("GET", "/records/externalintegration"),
        ("DELETE", "/Profile/Users/1"),
        ("PUT", "/Profile/ApprovalGroups/Update"),
        ("POST", "/Authorization/ChangeUserPassword"),
    ]

    def test_escritas_do_envision_sao_bloqueadas(self):
        for metodo, caminho in self.ESCRITAS_ENVISION:
            with self.subTest(metodo=metodo, caminho=caminho):
                with self.assertRaises(ChamadaBloqueadaError):
                    verificar_somente_leitura("Envision", metodo, caminho)

    def test_post_de_consulta_so_vale_para_o_envision(self):
        with self.assertRaises(ChamadaBloqueadaError):
            verificar_somente_leitura("RD Station CRM", "POST", "/Records/Query")

    def test_escritas_do_rd_sao_bloqueadas(self):
        for metodo, caminho in [("POST", "/contacts"), ("PUT", "/contacts/c1"), ("PUT", "/deals/d1"), ("POST", "/deals"), ("DELETE", "/deals/d1")]:
            with self.subTest(metodo=metodo, caminho=caminho):
                with self.assertRaises(ChamadaBloqueadaError):
                    verificar_somente_leitura("RD Station CRM", metodo, caminho)

    def test_leituras_permitidas_passam(self):
        for sistema, metodo, caminho in [
            ("Envision", "POST", "/Records/Query"),
            ("Envision", "GET", "/Records/123"),
            ("Envision", "GET", "/Records/GetServiceOrderSummaries"),
            ("RD Station CRM", "GET", "/contacts"),
        ]:
            verificar_somente_leitura(sistema, metodo, caminho)

    def test_conector_nao_alcanca_endpoint_fora_da_lista(self):
        conector, transporte = envision({})
        with self.assertRaises(ChamadaBloqueadaError):
            conector._http.get("/Profile/Users/1")
        with self.assertRaises(ChamadaBloqueadaError):
            conector._http.consultar_post("/Records", corpo={})
        self.assertEqual(transporte.requisicoes, [])


class TestErros(unittest.TestCase):
    def _erro(self, resposta, esperas=None):
        conector, transporte = rd({("GET", "/deals/d1"): resposta}, esperas)
        with self.assertRaises(IntegracaoError) as erro:
            conector.obter_negociacao("d1")
        return erro.exception, transporte

    def test_codigos_http(self):
        casos = {401: "credencial", 403: "permissao", 404: "nao_encontrado", 500: "servidor", 400: "resposta"}
        for status, tipo in casos.items():
            with self.subTest(status=status):
                erro, _ = self._erro(httpx.Response(status, json={"erro": "x"}))
                self.assertEqual(erro.tipo, tipo)

    def test_429_tenta_de_novo_uma_vez_e_depois_avisa(self):
        esperas = []
        resposta = [httpx.Response(429, headers={"Retry-After": "30"}), httpx.Response(429)]
        erro, transporte = self._erro(resposta, esperas)
        self.assertEqual(erro.tipo, "limite")
        self.assertEqual(len(transporte.requisicoes), 2)
        self.assertEqual(esperas, [5])  # espera limitada a 5 s

    def test_429_que_passa_na_segunda(self):
        conector, transporte = rd({("GET", "/deals/d1"): [httpx.Response(429), httpx.Response(200, json=fixture("rd_crm/deal_d1.json"))]})
        self.assertEqual(conector.obter_negociacao("d1")["id"], "d1")
        self.assertEqual(len(transporte.requisicoes), 2)

    def test_timeout_e_conexao(self):
        erro, _ = self._erro(httpx.ReadTimeout("lento"))
        self.assertEqual(erro.tipo, "timeout")
        erro, _ = self._erro(httpx.ConnectError("sem rede"))
        self.assertEqual(erro.tipo, "conexao")

    def test_credenciais_nunca_aparecem(self):
        saida = io.StringIO()
        handler = logging.StreamHandler(saida)
        logging.getLogger().addHandler(handler)
        try:
            with redirect_stdout(saida), redirect_stderr(saida):
                for resposta in [httpx.Response(401), httpx.Response(500), httpx.ReadTimeout("x"), httpx.ConnectError("x")]:
                    erro, _ = self._erro(resposta)
                    texto = str(erro) + repr(erro) + repr(erro.__cause__) + repr(erro.__context__)
                    self.assertNotIn(TOKEN_FICTICIO, texto)
                    self.assertNotIn("crm.rdstation.com", texto)
                    self.assertIsNone(erro.__cause__)
                    self.assertIsNone(erro.__context__)
                conector, _ = envision({("POST", "/Records/Query"): httpx.Response(401)})
                with self.assertRaises(IntegracaoError) as erro:
                    conector.consultar_registros("x")
                self.assertNotIn(CHAVE_FICTICIA, str(erro.exception) + repr(erro.exception))
        finally:
            logging.getLogger().removeHandler(handler)
        self.assertNotIn(TOKEN_FICTICIO, saida.getvalue())
        self.assertNotIn(CHAVE_FICTICIA, saida.getvalue())


if __name__ == "__main__":
    unittest.main()


class TestLoginEnvision(unittest.TestCase):
    """Login OAuth2 (usuário e senha em /token) com respostas fictícias."""

    USUARIO, SENHA = "agora-ficticio", "SENHA-FICTICIA-0000"
    TOKEN = "TOKEN-OAUTH-FICTICIO-0000"

    def conector(self, rotas, formato="senha"):
        transporte = TransporteFicticio(rotas)
        return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, usuario=self.USUARIO, senha=self.SENHA,
                        formato_chave=formato, transporte=transporte, esperar=lambda s: None), transporte

    def test_login_e_consultas_com_o_token(self):
        conector, transporte = self.conector({
            ("POST", "/token"): {"access_token": self.TOKEN, "token_type": "bearer", "expires_in": 3600},
            ("GET", "/Records/GetServiceOrderSummaries"): fixture("envision/summaries.json"),
            ("GET", "/Records/123"): fixture("envision/record_123.json"),
        })
        conector.testar_conexao()
        conector.obter_registro(123)
        login = transporte.requisicoes[0]
        self.assertEqual(login.url.path, "/token")
        corpo = login.content.decode()
        self.assertIn("grant_type=password", corpo)
        self.assertNotIn("client_id", corpo)
        self.assertEqual([r.url.path for r in transporte.requisicoes].count("/token"), 1)  # token reaproveitado
        self.assertEqual(transporte.requisicoes[1].headers["Authorization"], f"Bearer {self.TOKEN}")

    def test_login_com_a_chave_como_client_id(self):
        conector, transporte = self.conector({
            ("POST", "/token"): {"access_token": self.TOKEN, "expires_in": 3600},
            ("GET", "/Records/GetServiceOrderSummaries"): fixture("envision/summaries.json"),
        }, formato="senha_com_chave")
        conector.testar_conexao()
        self.assertIn("client_id=", transporte.requisicoes[0].content.decode())

    def test_login_recusado_sem_vazar_nada(self):
        conector, _ = self.conector({("POST", "/token"): httpx.Response(400, json={"error": "invalid_grant"})})
        with self.assertRaises(IntegracaoError) as erro:
            conector.testar_conexao()
        self.assertEqual(erro.exception.tipo, "credencial")
        texto = str(erro.exception) + repr(erro.exception)
        for segredo in (self.SENHA, self.USUARIO, CHAVE_FICTICIA):
            self.assertNotIn(segredo, texto)

    def test_resposta_sem_token(self):
        conector, _ = self.conector({("POST", "/token"): {"token_type": "bearer"}})
        with self.assertRaises(IntegracaoError) as erro:
            conector.testar_conexao()
        self.assertEqual(erro.exception.tipo, "resposta")

    def test_sem_usuario_e_senha(self):
        with self.assertRaises(IntegracaoError) as erro:
            Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, usuario="", senha="", formato_chave="senha")
        self.assertEqual(erro.exception.tipo, "credencial_ausente")

    def test_token_so_pelo_caminho_de_login(self):
        conector, transporte = self.conector({})
        with self.assertRaises(ChamadaBloqueadaError):
            conector._http.obter_token("/Records", {})
        with self.assertRaises(ChamadaBloqueadaError):
            conector._http.consultar_post("/token", corpo={})
        self.assertEqual(transporte.requisicoes, [])


class TestConfigEnvision(unittest.TestCase):
    """Nomes de variáveis do programador do formulário (08/10/2026)."""

    def test_nomes_alternativos_e_contexto(self):
        from unittest import mock as _mock
        from integracoes import config
        valores = {"ENVISION_USUARIO": "", "ENVISION_SENHA": "", "ENVISION_USERNAME": "u-ficticio", "ENVISION_PASSWORD": "s-ficticia",
                   "ENVISION_TRAVEL_AGENCY_ID": "160779", "ENVISION_TRAVEL_AGENCY_SYSTEM_ACCOUNT_ID": "38144"}
        with _mock.patch.dict("os.environ", valores):
            self.assertEqual(config.envision_usuario(), "u-ficticio")
            self.assertEqual(config.envision_senha(), "s-ficticia")
            self.assertEqual(config.envision_formato_chave(), "senha")
            self.assertEqual(config.envision_contexto(), {"travelAgencyId": 160779, "systemAccountId": 38144})

    def test_additional_info_vai_na_consulta(self):
        import json as _json
        from unittest import mock as _mock
        corpos = []

        def responder(req):
            corpos.append(_json.loads(req.content))
            return httpx.Response(200, json={"records": [], "successful": True})

        with _mock.patch.dict("os.environ", {"ENVISION_TRAVEL_AGENCY_ID": "160779", "ENVISION_SYSTEM_ACCOUNT_ID": "38144"}):
            conector = Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", transporte=httpx.MockTransport(responder))
            conector.consultar_registros('externalId = "00000000000"')
            conector.consultar_registros('externalId = "00000000000"', info_adicional={})
        self.assertEqual(corpos[0]["additionalInfo"], {"travelAgencyId": "160779", "systemAccountId": "38144"})
        self.assertNotIn("additionalInfo", corpos[1])



class TestSessaoEnvision(unittest.TestCase):
    """GetSession depois do login, com o contexto da agência (08/10/2026)."""

    def test_sessao_aberta_uma_vez_depois_do_login(self):
        import json as _json
        rotas = []

        def responder(req):
            rotas.append((req.method, req.url.path))
            if req.url.path == "/token":
                return httpx.Response(200, json={"access_token": "TOKEN-X", "expires_in": 3600})
            if req.url.path == "/Authorization/GetSession":
                corpo = _json.loads(req.content)
                assert corpo["companyContext"]["travelAgency"] == {"id": 160779, "systemAccountId": 38144}
                assert req.headers["Authorization"] == "Bearer TOKEN-X"
                return httpx.Response(200, json={"session": {}, "successful": True})
            return httpx.Response(200, json={"records": [], "successful": True})

        conector = Envision(base_url=BASE_ENVISION_FICTICIA, usuario="u", senha="s", formato_chave="senha",
                            transporte=httpx.MockTransport(responder), contexto={"travelAgencyId": 160779, "systemAccountId": 38144})
        conector.consultar_registros('externalId = "0"')
        conector.consultar_registros('externalId = "1"')
        self.assertEqual(rotas, [("POST", "/token"), ("POST", "/Authorization/GetSession"), ("POST", "/Records/Query"), ("POST", "/Records/Query")])

    def test_sem_contexto_nao_abre_sessao(self):
        rotas = []

        def responder(req):
            rotas.append(req.url.path)
            if req.url.path == "/token":
                return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
            return httpx.Response(200, json={"records": [], "successful": True})

        conector = Envision(base_url=BASE_ENVISION_FICTICIA, usuario="u", senha="s", formato_chave="senha",
                            transporte=httpx.MockTransport(responder), contexto={})
        conector.consultar_registros("x")
        self.assertEqual(rotas, ["/token", "/Records/Query"])

    def test_outros_authorization_continuam_bloqueados(self):
        conector = Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura",
                            transporte=httpx.MockTransport(lambda r: httpx.Response(500)), contexto={})
        for caminho in ("/Authorization/ChangeUserPassword", "/Authorization/ResetUserPassword", "/Records"):
            with self.subTest(caminho=caminho):
                with self.assertRaises(ChamadaBloqueadaError):
                    conector._http.iniciar_sessao(caminho, corpo={})
                with self.assertRaises(ChamadaBloqueadaError):
                    verificar_somente_leitura("Envision", "POST", caminho)



class TestVendasPorCpf(unittest.TestCase):
    """Caminho do formulário de viajantes (nível 10, 08/10/2026)."""

    def conector(self, roteador, chamadas):
        def registrar(req):
            chamadas.append(req)
            return roteador(req)

        return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", contexto={},
                        transporte=httpx.MockTransport(registrar))

    def test_cadastro_ate_as_vendas(self):
        pessoa = {"id": 1, "type": "Person", "externalId": "123.456.789-09", "associations": [
            {"recordAssociatedId": 50, "recordType": "ServiceOrder"},
            {"recordAssociatedId": 50, "recordType": "ServiceOrder"},
            {"recordAssociatedId": 60, "recordType": "ServiceBook"}]}

        def roteador(req):
            if req.url.path == "/Records/Query":
                self.assertEqual(json.loads(req.content)["criteria"], 'ExternalId = "12345678909"')
                return httpx.Response(200, json={"records": [pessoa], "successful": True})
            if req.url.path == "/Records/50":
                return httpx.Response(200, json={"id": 50, "type": "ServiceOrder"})
            return httpx.Response(404)

        chamadas = []
        tem, vendas = self.conector(roteador, chamadas).vendas_por_cpf("123.456.789-09")
        self.assertTrue(tem)
        self.assertEqual([v["id"] for v in vendas], [50])
        self.assertEqual([(c.method, c.url.path) for c in chamadas], [("POST", "/Records/Query"), ("GET", "/Records/50")])

    def test_filtro_ignorado_nao_traz_outra_pessoa(self):
        outra = {"id": 2, "type": "Person", "externalId": "99999999999",
                 "associations": [{"recordAssociatedId": 70, "recordType": "ServiceOrder"}]}
        chamadas = []
        conector = self.conector(lambda req: httpx.Response(200, json={"records": [outra], "successful": True}), chamadas)
        self.assertEqual(conector.vendas_por_cpf("12345678909"), (False, []))
        self.assertEqual(len(chamadas), 1)

    def test_cpf_invalido_nao_consulta(self):
        chamadas = []
        conector = self.conector(lambda req: httpx.Response(500), chamadas)
        for cpf in ("", None, "123", '1" || Created > "2000'):
            self.assertEqual(conector.vendas_por_cpf(cpf), (False, []))
        self.assertEqual(chamadas, [])


class TestAjustesProgramador(unittest.TestCase):
    def test_timeout_maior_no_envision(self):
        from integracoes.envision import TIMEOUT_ENVISION_SEGUNDOS
        conector = Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", contexto={})
        self.assertEqual(conector._http._timeout, TIMEOUT_ENVISION_SEGUNDOS)
        self.assertEqual(TIMEOUT_ENVISION_SEGUNDOS, 30)

    def test_motivo_da_recusa_sem_dados_pessoais(self):
        resposta = {"records": [], "successful": False,
                    "errors": [{"code": "E1", "message": "criteria inválido: externalId = 123.456.789-09 de ana@exemplo.com"}]}
        conector = Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", contexto={},
                            transporte=httpx.MockTransport(lambda r: httpx.Response(200, json=resposta)))
        saida = io.StringIO()
        with redirect_stdout(saida):
            with self.assertRaises(IntegracaoError):
                conector.consultar_registros("x")
        texto = saida.getvalue()
        self.assertIn("E1: criteria inválido", texto)
        self.assertNotIn("123.456.789-09", texto)
        self.assertNotIn("ana@exemplo.com", texto)
