"""Camada 4: script de verificação, com conectores sobre respostas fictícias."""

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import httpx

from integracoes.envision import Envision
from integracoes.rd_crm import RDCRM
from tests.apoio import BASE_ENVISION_FICTICIA, CHAVE_FICTICIA, TOKEN_FICTICIO, TransporteFicticio, bloquear_internet, fixture

bloquear_internet()

import scripts.verificar_integracoes as script  # noqa: E402

CAMPOS_RD = [{"id": "cpf_field", "label": "CPF", "type": "text"}]
REGISTRO_FIT = dict(fixture("envision/record_123.json"), id=124, summary="FIT JOAO EXEMPLO JAPAO SET 25")
REGISTRO_RUIM = dict(fixture("envision/record_123.json"), id=125, summary="CRUZEIRO CARIBE")
QUERY = {"resposta": None}
RESUMOS = {"items": [{"id": 123, "passengers": ["João Exemplo"]}, {"id": 124, "passengers": ["João Exemplo"]}, {"id": 125, "passengers": ["Outra Pessoa"]}], "successful": True}


def rotas_rd():
    return {
        ("GET", "/contacts"): fixture("rd_crm/contacts_joao.json"),
        ("GET", "/deals/d1"): fixture("rd_crm/deal_d1.json"),
        ("GET", "/deals/d2"): fixture("rd_crm/deal_d1.json"),
        ("GET", "/deal_pipelines"): fixture("rd_crm/deal_pipelines.json"),
        ("GET", "/custom_fields"): CAMPOS_RD,
    }


def rotas_envision(formato_ok="pura"):
    autorizado = {"pura": CHAVE_FICTICIA, "bearer": f"Bearer {CHAVE_FICTICIA}"}[formato_ok]

    def resposta(dados):
        def responder(req):
            return httpx.Response(200, json=dados) if req.headers.get("Authorization") == autorizado else httpx.Response(401)
        return responder

    return resposta


def executar(*args, formato_ok="pura"):
    responder = rotas_envision(formato_ok)

    def envision_ficticio(formato_chave=None, **kwargs):
        def roteador(req):
            caminho = req.url.path
            dados = {"/Records/GetServiceOrderSummaries": RESUMOS, "/Records/Query": QUERY["resposta"] or fixture("envision/query_records.json"), "/Records/123": fixture("envision/record_123.json"),
                     "/Records/124": REGISTRO_FIT, "/Records/125": REGISTRO_RUIM}.get(caminho)
            return responder(dados)(req) if dados is not None else httpx.Response(404)
        return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave=formato_chave or "pura", transporte=httpx.MockTransport(roteador))

    rd_ficticio = lambda: RDCRM(token=TOKEN_FICTICIO, transporte=TransporteFicticio(rotas_rd()))
    saida = io.StringIO()
    with mock.patch.object(script, "RDCRM", rd_ficticio), mock.patch.object(script, "Envision", envision_ficticio), redirect_stdout(saida):
        script.main(list(args))
    texto = saida.getvalue()
    assert TOKEN_FICTICIO not in texto and CHAVE_FICTICIA not in texto, "credencial impressa!"
    return texto


class TestScript(unittest.TestCase):
    def test_nivel_1_conecta_e_descobre_o_formato(self):
        texto = executar("--nivel", "1", formato_ok="bearer")
        self.assertIn("RD Station CRM: conectado", texto)
        self.assertIn("Envision: conectado (formato da chave: bearer)", texto)
        self.assertIn("ENVISION_AUTH_FORMATO=bearer", texto)

    def test_nivel_1_mostra_erro_sem_credencial(self):
        sem_credencial = mock.Mock(side_effect=script.IntegracaoError("RD Station CRM", "credencial_ausente"))
        sem_chave = mock.Mock(side_effect=script.IntegracaoError("Envision", "credencial"))
        saida = io.StringIO()
        with mock.patch.object(script, "RDCRM", sem_credencial), mock.patch.object(script, "Envision", sem_chave), redirect_stdout(saida):
            script.main(["--nivel", "1"])
        self.assertIn("RD Station CRM: ERRO — Credencial do RD Station CRM não configurada", saida.getvalue())
        for formato in ("senha", "senha_com_chave", "pura", "bearer"):
            self.assertIn(f"Envision (chave {formato}): ERRO — O Envision recusou a credencial (401).", saida.getvalue())

    def test_nivel_2_so_nomes_e_tipos(self):
        texto = executar("--nivel", "2", "--limite", "3")
        for valor in ["João Exemplo", "Joao.Exemplo", "123.456.789-09", "12345678909", "11999990000", "Anotação livre", "Rua Fictícia", "45000", "GRP ARMENIA", "Consultora Fictícia", "Emitido"]:
            with self.subTest(valor=valor):
                self.assertNotIn(valor, texto)
        self.assertIn("emails[].email: texto", texto)
        self.assertIn("(parece CPF)", texto)
        self.assertIn("summary: texto  (parece nome de produto", texto)
        self.assertIn('id cpf_field: "CPF" (text)', texto)
        self.assertIn("travellers[].fullName: texto", texto)

    def test_nivel_3_classifica_e_destaca(self):
        consulta = {"records": [fixture("envision/record_123.json"), REGISTRO_FIT, REGISTRO_RUIM], "successful": True}
        with mock.patch.dict(QUERY, {"resposta": consulta}):
            texto = executar("--nivel", "3", "--limite", "10")
        self.assertIn("Vendas | Grupo -> Grupo", texto)
        self.assertIn("Funil Novo Não Mapeado -> Outros   <-- NÃO MAPEADO", texto)
        self.assertIn("ServiceOrder: 3 registro(s); com nome de produto: 2; com valor: 3", texto)
        self.assertIn("status: Emitido", texto)
        self.assertIn("GRP ARMENIA E GEORGIA MB SET 27  [ServiceOrder] -> Grupo", texto)
        self.assertIn("CRUZEIRO CARIBE  [ServiceOrder] -> Não classificado   <-- NÃO CLASSIFICADO", texto)
        self.assertIn("Não classificados: 1 de 3", texto)
        self.assertNotIn("12345678909", texto)

    def test_nivel_4_perfil_por_email(self):
        mapa = json.loads(json.dumps(script.carregar_mapeamentos()))
        mapa["rd_campos"]["contato_cpf_custom_field_id"] = "cpf_field"
        with mock.patch("services.perfil_cliente.carregar_mapeamentos", return_value=mapa), \
             mock.patch.dict(QUERY, {"resposta": fixture("envision/person_cpf.json")}):
            texto = executar("--nivel", "4", "--email", "joao.exemplo@exemplo.com", "--historico-completo")
        self.assertIn("Cliente: João Exemplo — joao.exemplo@exemplo.com", texto)
        self.assertIn("Encontrado em: RD Station CRM, Envision", texto)
        self.assertNotIn("12345678909", texto)


if __name__ == "__main__":
    unittest.main()


class TestNivel5(unittest.TestCase):
    def test_descobre_filtros_sem_mostrar_o_email(self):
        email = "passageiro.secreto@exemplo.com"
        os_1 = dict(fixture("envision/record_123.json"), type="ServiceOrder", passengersList=[{"PassengerEmail": email}])
        os_2 = dict(fixture("envision/record_123.json"), id=999, type="ServiceOrder", passengersList=[{"PassengerEmail": "outro@exemplo.com"}])
        servico = dict(fixture("envision/record_123.json"), id=555, type="ServiceBook", passengersList=[{"PassengerEmail": email}])
        consultas = []

        def roteador(req):
            criteria = json.loads(req.content)["criteria"]
            consultas.append(criteria)
            registros = []
            if criteria.startswith("Created >="):
                registros = [os_1, os_2, servico]
            elif "RecordType = 'ServiceOrder'" in criteria:
                registros = [os_1, os_2]
            elif criteria.startswith("PassengerEmail ="):
                registros = [os_1, servico]
            elif criteria.startswith("Passenger ="):
                registros = [os_1, os_2]
            return httpx.Response(200, json={"records": registros, "successful": True})

        def envision(**kw):
            return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", transporte=httpx.MockTransport(roteador))

        saida = io.StringIO()
        with mock.patch.object(script, "Envision", envision), redirect_stdout(saida):
            script.main(["--nivel", "5"])
        texto = saida.getvalue()
        self.assertIn("Controle — últimos 30 dias: 3 registro(s)", texto)
        self.assertIn("RecordType = 'ServiceOrder': 2 registro(s), tipos ServiceOrder  <-- FUNCIONA", texto)
        self.assertIn("Type = 'ServiceOrder': 0 registro(s)", texto)
        self.assertIn("PassengerEmail: FUNCIONA — 2 registro(s), todos desse passageiro", texto)
        self.assertIn("Passenger: ignorado — 2 registro(s), só 1", texto)
        self.assertNotIn(email, texto)
        self.assertEqual(len(consultas), 1 + 1 + 4 + 7)

    def test_email_com_aspas_e_recusado(self):
        def envision(**kw):
            return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura",
                            transporte=httpx.MockTransport(lambda req: httpx.Response(200, json={"records": [], "successful": True})))

        saida = io.StringIO()
        with mock.patch.object(script, "Envision", envision), redirect_stdout(saida):
            script.main(["--nivel", "5", "--email", "x' || Type = 'y@a.com"])
        self.assertIn("E-mail inválido", saida.getvalue())


class TestNivel5Nome(unittest.TestCase):
    def test_filtro_por_nome(self):
        pessoa = {"id": 7, "type": "Person", "fullName": "Ana Souza Teste"}
        outra = {"id": 8, "type": "Person", "fullName": "Outra Pessoa"}

        def roteador(req):
            criteria = json.loads(req.content)["criteria"]
            if criteria.startswith("Person.FullName ="):
                registros = [pessoa]
            elif criteria.startswith("FullName ="):
                registros = [pessoa, outra]
            else:
                registros = []
            return httpx.Response(200, json={"records": registros, "successful": True})

        def envision(**kw):
            return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", transporte=httpx.MockTransport(roteador))

        saida = io.StringIO()
        with mock.patch.object(script, "Envision", envision), redirect_stdout(saida):
            script.main(["--nivel", "5", "--nome", "Ana Souza Teste"])
        texto = saida.getvalue()
        self.assertIn("Person.FullName = 'NOME': FUNCIONA — 1 registro(s), todos com esse nome; tipos Person", texto)
        self.assertIn("FullName = 'NOME': ignorado — 2 registro(s), só 1", texto)
        self.assertNotIn("Ana Souza", texto)

    def test_nome_com_aspas_e_recusado(self):
        saida = io.StringIO()
        with redirect_stdout(saida):
            script.main(["--nivel", "5", "--nome", "x' || Type = 'y"])
        self.assertIn("Nome inválido", saida.getvalue())


class TestNivel5Cpf(unittest.TestCase):
    def test_formato_do_programador(self):
        cpf = "12345678909"
        pessoa = {"id": 7, "type": "Person", "externalId": "123.456.789-09", "associations": [{"recordAssociatedId": 123, "recordType": "ServiceOrder"}]}
        corpos = []

        def roteador(req):
            corpo = json.loads(req.content)
            corpos.append(corpo)
            criteria = corpo["criteria"]
            if criteria.startswith("Created"):
                registros = [dict(fixture("envision/record_123.json"), travelAgencyId=11, systemAccountId=22)]
            elif criteria == 'externalId = "123.456.789-09"' and corpo.get("additionalInfo") == {"travelAgencyId": "11", "systemAccountId": "22"}:
                registros = [pessoa]
            else:
                registros = []
            return httpx.Response(200, json={"records": registros, "successful": True})

        def envision(**kw):
            return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", transporte=httpx.MockTransport(roteador))

        saida = io.StringIO()
        with mock.patch.object(script, "Envision", envision), redirect_stdout(saida):
            script.main(["--nivel", "5", "--cpf", "123.456.789-09"])
        texto = saida.getvalue()
        self.assertIn("agência/conta dos registros", texto)
        self.assertIn("externalId só números (com additionalInfo): 0 registro(s)", texto)
        self.assertIn("externalId com pontuação (com additionalInfo): 1 registro(s) — tipos Person: 1; associações: 1", texto)
        self.assertIn("externalId com pontuação (sem additionalInfo): 0 registro(s)", texto)
        self.assertNotIn(cpf, texto)
        self.assertNotIn("123.456.789-09", texto)
        self.assertEqual(corpos[1]["pagingPivotId"], 0)
        self.assertEqual(corpos[1]["sortFields"], [])


class TestNivel6(unittest.TestCase):
    def test_formato_sem_valores(self):
        registro = dict(fixture("envision/record_123.json"), type="Person", externalId="12345678909")
        registro["travellers"] = [{"externalId": "ana@exemplo.com", "documents": [{"type": "CPF", "value": "987.654.321-00"}]}]

        def envision(**kw):
            return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura",
                            transporte=httpx.MockTransport(lambda req: httpx.Response(200, json={"records": [registro], "successful": True})))

        saida = io.StringIO()
        with mock.patch.object(script, "Envision", envision), redirect_stdout(saida):
            script.main(["--nivel", "6"])
        texto = saida.getvalue()
        self.assertIn("Person — externalId do registro: parece CPF (só números) (1x)", texto)
        self.assertIn("Person — travellers[].externalId: parece e-mail (1x)", texto)
        self.assertIn("Person — documento CPF: parece CPF (com pontuação) (1x)", texto)
        for valor in ("12345678909", "ana@exemplo.com", "987.654.321-00"):
            self.assertNotIn(valor, texto)


class TestNivel5CpfAuto(unittest.TestCase):
    def test_controle_positivo_com_cadastro_existente(self):
        pessoa = {"id": 7, "type": "Person", "externalId": "12345678909", "travelAgencyId": 11, "systemAccountId": 22,
                  "associations": [{"recordAssociatedId": 123, "recordType": "ServiceOrder"}]}

        def roteador(req):
            corpo = json.loads(req.content)
            criteria = corpo["criteria"]
            if criteria.startswith("Created"):
                registros = [pessoa]
            elif criteria == 'externalId = "12345678909"' and corpo.get("additionalInfo"):
                registros = [pessoa]
            else:
                registros = []
            return httpx.Response(200, json={"records": registros, "successful": True})

        def envision(**kw):
            return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", transporte=httpx.MockTransport(roteador))

        saida = io.StringIO()
        with mock.patch.object(script, "Envision", envision), redirect_stdout(saida):
            script.main(["--nivel", "5", "--cpf", "auto"])
        texto = saida.getvalue()
        self.assertIn("controle positivo", texto)
        self.assertIn("externalId só números (com additionalInfo): 1 registro(s) — tipos Person: 1; associações: 1 (ServiceOrder)", texto)
        self.assertNotIn("12345678909", texto)


class TestNivel7(unittest.TestCase):
    def test_requisicoes_impressas_sem_segredos(self):
        import os as _os
        cpf = "12345678909"
        pessoa = {"id": 7, "type": "Person", "externalId": cpf}

        def responder(self_transporte, request):
            caminho = request.url.path
            if caminho == "/token":
                return httpx.Response(200, json={"access_token": "TOKEN-SECRETO", "expires_in": 3600})
            if caminho == "/Authorization/GetSession":
                return httpx.Response(200, json={"session": {"userId": 1}, "successful": True})
            corpo = json.loads(request.read())
            if corpo["criteria"].startswith("Created"):
                return httpx.Response(200, json={"records": [pessoa], "successful": True, "messages": [{"text": f"cpf {cpf} ok"}]})
            return httpx.Response(200, json={"records": [], "successful": True, "warnings": [{"text": "aviso x"}]})

        valores = {"ENVISION_BASE_URL": BASE_ENVISION_FICTICIA, "ENVISION_USERNAME": "usuario-secreto", "ENVISION_PASSWORD": "senha-secreta",
                   "ENVISION_TRAVEL_AGENCY_ID": "160779", "ENVISION_SYSTEM_ACCOUNT_ID": "38144"}
        saida = io.StringIO()
        with mock.patch.dict(_os.environ, valores), \
             mock.patch("httpx.HTTPTransport.handle_request", responder), \
             redirect_stdout(saida):
            script.main(["--nivel", "7"])
        texto = saida.getvalue()
        for segredo in ("usuario-secreto", "senha-secreta", "TOKEN-SECRETO", cpf):
            self.assertNotIn(segredo, texto)
        self.assertIn(">>> POST https://envision.exemplo.invalid/token", texto)
        self.assertIn(">>> POST https://envision.exemplo.invalid/Authorization/GetSession", texto)
        self.assertIn('"travelAgencyId":"160779"', texto.replace(" ", ""))
        self.assertIn("aviso x", texto)
        self.assertIn("busca pelo externalId", texto)



class TestNivel8(unittest.TestCase):
    def test_abordagem_do_programador_so_contagens(self):
        def roteador(req):
            if req.url.path == "/Records/GetServiceOrderSummaries":
                return httpx.Response(200, json={"items": [
                    {"id": 1, "passengers": ["ANA SOUZA TESTE", "Outro"]},
                    {"id": 2, "passengers": [{"fullName": "Fulano"}], "corporateCustomerName": "Ana Souza Teste"},
                    {"id": 3, "passengers": ["Ninguém"]}], "successful": True})
            if req.url.path == "/Shopping/QueryTravellers":
                corpo = json.loads(req.content)
                assert corpo["travellerName"] == "Ana Souza Teste"
                return httpx.Response(200, json={"travellers": [{"id": 9, "name": "Ana Souza Teste"}], "successful": True})
            return httpx.Response(404)

        def envision(**kw):
            return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", contexto={},
                            transporte=httpx.MockTransport(roteador))

        saida = io.StringIO()
        with mock.patch.object(script, "Envision", envision), redirect_stdout(saida):
            script.main(["--nivel", "8", "--nome", "Ana Souza Teste"])
        texto = saida.getvalue()
        self.assertIn("3 ordem(ns) no total; 2 com esse nome", texto)
        self.assertIn("1 viajante(s); 1 com esse nome", texto)
        self.assertNotIn("Ana Souza", texto)

    def test_query_travellers_e_leitura_liberada_so_no_envision(self):
        from integracoes.http import verificar_somente_leitura
        verificar_somente_leitura("Envision", "POST", "/Shopping/QueryTravellers")
        for caminho in ("/Shopping/PreOrder", "/Shopping/Search"):
            with self.assertRaises(script.ChamadaBloqueadaError):
                verificar_somente_leitura("Envision", "POST", caminho)



class TestNivel9(unittest.TestCase):
    def test_duplicados_e_variacoes(self):
        pessoas = [{"id": i, "type": "Person", "externalId": cpf} for i, cpf in enumerate(["11111111111", "11111111111", "22222222222"])]

        def roteador(req):
            criteria = json.loads(req.content)["criteria"]
            if criteria.startswith("Created"):
                return httpx.Response(200, json={"records": pessoas, "successful": True})
            if criteria == "externalId = '11111111111'":
                return httpx.Response(200, json={"records": pessoas[:2], "successful": True})
            return httpx.Response(200, json={"records": [], "successful": True})

        def envision(**kw):
            return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", contexto={},
                            transporte=httpx.MockTransport(roteador))

        saida = io.StringIO()
        with mock.patch.object(script, "Envision", envision), redirect_stdout(saida):
            script.main(["--nivel", "9"])
        texto = saida.getvalue()
        self.assertIn("Cadastros Person com CPF (últimos 90 dias, até 5 páginas): 3", texto)
        self.assertIn("CPFs que aparecem em mais de um cadastro: 1", texto)
        self.assertIn("externalId com aspas simples: 2 registro(s), tipos Person  <-- FUNCIONA", texto)
        self.assertIn("ExternalId com aspas duplas: 0 registro(s)", texto)
        self.assertNotIn("11111111111", texto)



class TestNivel10(unittest.TestCase):
    def test_cadastro_ate_as_viagens(self):
        cpf = "11111111111"
        pessoa = {"id": 1, "type": "Person", "externalId": cpf,
                  "associations": [{"recordAssociatedId": 50, "recordType": "ServiceOrder", "type": "Traveller"},
                                   {"recordAssociatedId": 60, "recordType": "ServiceBook", "type": "Traveller"}]}
        ordem = {"id": 50, "type": "ServiceOrder", "status": {"name": "Fechada"}, "totalValue": {"value": 10.0},
                 "travellers": [{}, {}], "startDate": {"year": 2026},
                 "children": [{"type": "ItineraryBook", "summary": "GRP JAPAO LF MAI 27"},
                              {"type": "ServiceBook", "destination": {"name": "Japão"}}]}

        def roteador(req):
            if req.url.path == "/Records/50":
                return httpx.Response(200, json=ordem)
            criteria = json.loads(req.content)["criteria"]
            if criteria.startswith("Created"):
                return httpx.Response(200, json={"records": [pessoa], "successful": True})
            if criteria == f'ExternalId = "{cpf}"':
                return httpx.Response(200, json={"records": [pessoa], "successful": True})
            return httpx.Response(200, json={"records": [], "successful": True})

        def envision(**kw):
            return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", contexto={},
                            transporte=httpx.MockTransport(roteador))

        saida = io.StringIO()
        with mock.patch.object(script, "Envision", envision), redirect_stdout(saida):
            script.main(["--nivel", "10"])
        texto = saida.getvalue()
        self.assertIn("Cadastros encontrados pelo ExternalId: 1", texto)
        self.assertIn("ServiceOrder: 1 (tipo de ligação: Traveller)", texto)
        self.assertIn("status Fechada; valor sim; viajantes 2; data de início sim; filhos ItineraryBook: 1, ServiceBook: 1", texto)
        self.assertIn("produto do ItineraryBook: tipo Grupo, mês/ano sim", texto)
        self.assertIn("serviços com destino: 1", texto)
        self.assertNotIn(cpf, texto)



class TestNivel11(unittest.TestCase):
    def test_controle_positivo_pelo_viajante(self):
        venda = {"id": 50, "type": "ServiceOrder",
                 "travellers": [{"fullName": "Ana Souza Teste", "documents": [{"type": "Cpf", "value": "111.222.333-44"}]}]}

        def roteador(req):
            criteria = json.loads(req.content)["criteria"]
            if criteria.startswith("Created"):
                return httpx.Response(200, json={"records": [venda], "successful": True})
            if criteria == "TravellerName = 'Ana Souza Teste'":
                return httpx.Response(200, json={"records": [venda], "successful": True})
            if criteria == "Cpf = '111.222.333-44'":
                return httpx.Response(200, json={"records": [venda, {"id": 99, "type": "ServiceOrder"}], "successful": True})
            return httpx.Response(200, json={"records": [], "successful": True})

        def envision(**kw):
            return Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", contexto={},
                            transporte=httpx.MockTransport(roteador))

        saida = io.StringIO()
        with mock.patch.object(script, "Envision", envision), redirect_stdout(saida):
            script.main(["--nivel", "11"])
        texto = saida.getvalue()
        self.assertIn("TravellerName: FUNCIONA — 1 registro(s), inclui a venda de controle", texto)
        self.assertIn("PassengerName: 0 registros", texto)
        self.assertIn("Cpf: ignorado — 2 registro(s), só 1 com o valor", texto)
        self.assertNotIn("Ana Souza", texto)
        self.assertNotIn("111.222.333-44", texto)


class TestNivel12(unittest.TestCase):
    def test_negociacoes_na_busca_na_ficha_e_em_outro_contato(self):
        mapa = json.loads(json.dumps(script.carregar_mapeamentos()))
        mapa["rd_campos"]["contato_cpf_custom_field_id"] = "cf_cpf"
        cpf = [{"custom_field_id": "cf_cpf", "value": "123.456.789-09"}]
        principal = {"id": "c1", "name": "Ana Souza Teste", "emails": [{"email": "ana@exemplo.com"}], "phones": [{"phone": "+55 (11) 99999-1234"}],
                     "contact_custom_fields": cpf, "deals": [{"_id": "d1"}]}
        ficha = dict(principal, deals=[{"_id": "d1"}, {"_id": "d2"}])
        duplicado = {"id": "c2", "name": "ana souza teste", "emails": [], "phones": [{"phone": "11 9999-1234"}], "contact_custom_fields": cpf, "deals": [{"_id": "d3"}]}
        funil = {"d1": "Formulários", "d2": "Vendas | Grupo", "d3": "Vendas | Grupo"}

        def roteador(req):
            caminho = req.url.path.split("/api/v1", 1)[-1]
            if caminho == "/contacts":
                if req.url.params.get("email"):
                    return httpx.Response(200, json={"contacts": [principal]})
                return httpx.Response(200, json={"contacts": [principal, duplicado]})
            if caminho == "/contacts/c1":
                return httpx.Response(200, json=ficha)
            if caminho.startswith("/deals/"):
                return httpx.Response(200, json={"deal_pipeline": {"name": funil[caminho.split("/")[-1]]}})
            return httpx.Response(404)

        saida = io.StringIO()
        with mock.patch.object(script, "RDCRM", lambda: RDCRM(token=TOKEN_FICTICIO, transporte=httpx.MockTransport(roteador))),              mock.patch.object(script, "carregar_mapeamentos", return_value=mapa), redirect_stdout(saida):
            script.main(["--nivel", "12", "--email", "ana@exemplo.com"])
        texto = saida.getvalue()
        self.assertIn("Negociações na busca: 1; na ficha do contato: 2", texto)
        self.assertIn("funis: Formulários: 1, Vendas | Grupo: 1", texto)
        self.assertIn("Outros contatos com o mesmo nome: 1", texto)
        self.assertIn("contato: mesmo CPF sim; mesmo e-mail sem dado; mesmo telefone sim; negociações 1 (Vendas | Grupo: 1)", texto)
        for dado in ("Ana Souza", "ana@exemplo.com", "123.456.789-09", "12345678909", "99999-1234", "1234"):
            self.assertNotIn(dado, texto)
