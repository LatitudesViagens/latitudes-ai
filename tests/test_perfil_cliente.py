"""Camada 2: regras de negócio do perfil do cliente, com dados fictícios."""

from dataclasses import asdict
from datetime import date
import json
import unittest

from integracoes.erros import IntegracaoError
from services.perfil_cliente import (
    SEM_CADASTRO_ENVISION,
    SEM_CPF_NO_RD,
    BuscadorPerfil,
    calcular_viagens,
    carregar_mapeamentos,
    classificar_funil,
    classificar_produto,
    normalizar_cpf,
    normalizar_email,
    resumo_para_ia,
    viagem_de_registro,
)
from tests.apoio import bloquear_internet

bloquear_internet()

HOJE = date(2026, 10, 7)
MAP = carregar_mapeamentos()
# Nos testes, o CPF do contato do RD fica no campo personalizado "cf_cpf".
MAP_COM_CPF = json.loads(json.dumps(MAP))
MAP_COM_CPF["rd_campos"]["contato_cpf_custom_field_id"] = "cf_cpf"

SENSIVEIS = ["123.456.789-09", "12345678909", "98765432100", "11999990000", "Rua Fictícia", "1980", "Anotação livre", "PAGAMENTO-SECRETO"]


# --- Construtores de dados fictícios no formato das APIs ---------------------------
def contato(cid, nome, email, cpf=None, deals=(), telefone="11999990000"):
    return {
        "id": cid, "_id": cid, "name": nome,
        "emails": [{"email": email}] if email else [],
        "phones": [{"phone": telefone}],
        "birthday": {"day": 1, "month": 1, "year": 1980},
        "notes": "Anotação livre FICTÍCIA",
        "contact_custom_fields": [{"custom_field_id": "cf_cpf", "value": cpf}] if cpf else [],
        "deals": [{"_id": d} for d in deals],
    }


def deal(did, funil, win=None, etapa="Proposta", valor=10000.0, criado="2026-01-10T10:00:00.000-03:00"):
    return {
        "id": did, "name": "nome livre", "deal_pipeline": {"name": funil}, "deal_stage": {"name": etapa},
        "user": {"name": "Consultora Fictícia"}, "win": win, "closed_at": None if win is None else "2026-02-01T10:00:00.000-03:00",
        "amount_total": valor, "created_at": criado, "prediction_date": None,
        "deal_custom_fields": [{"custom_field_id": "obs", "value": "Anotação livre no campo"}],
    }


def ordem(oid, produto, viajantes, valor=10000.0, moeda="BRL", inicio=(2026, 5, 10), status="Emitido", criado=(2026, 1, 5)):
    return {
        "id": oid, "summary": produto, "status": {"name": status, "code": status.upper()},
        "totalValue": {"value": valor, "currencyCode": moeda} if valor is not None else None,
        "startDate": {"year": inicio[0], "month": inicio[1], "day": inicio[2]} if inicio else None,
        "createdTime": {"year": criado[0], "month": criado[1], "day": criado[2]} if criado else None,
        "travellers": [
            {"fullName": nome, "documents": [{"type": "CPF", "number": cpf}] if cpf else [],
             "birthDate": {"year": 1980, "month": 1, "day": 1}, "address": {"street": "Rua Fictícia, 1"}}
            for nome, cpf in viajantes
        ],
        "payments": [{"info": "PAGAMENTO-SECRETO"}],
    }


class RDFalso:
    def __init__(self, contatos=(), deals=(), erro=None):
        self.contatos, self.deals, self.erro = list(contatos), {d["id"]: d for d in deals}, erro

    def buscar_contatos(self, nome=None, email=None):
        if self.erro:
            raise self.erro
        return self.contatos

    def obter_negociacao(self, did):
        return self.deals[did]


class EnvisionFalso:
    """Cadastro Person = algum viajante das vendas com o CPF, ou CPF em
    `cadastros` (cadastro sem vendas)."""

    def __init__(self, ordens=(), erro=None, cadastros=()):
        self.ordens, self.erro = {o["id"]: o for o in ordens}, erro
        self.cadastros = set(cadastros)
        self.consultas: list[str] = []

    def vendas_por_cpf(self, cpf):
        self.consultas.append(cpf)
        if self.erro:
            raise self.erro
        vendas = [
            o for o in self.ordens.values()
            if any(normalizar_cpf(d.get("number") or d.get("value")) == cpf
                   for t in o["travellers"] for d in t.get("documents") or [])
        ]
        return bool(vendas) or cpf in self.cadastros, vendas


def buscar(rd, env, texto="João Exemplo", mapa=MAP_COM_CPF, **kwargs):
    return BuscadorPerfil(rd=rd, envision=env, mapeamentos=mapa, hoje=HOJE).buscar(texto, **kwargs)


# --- Testes -----------------------------------------------------------------------------
class TestClassificacao(unittest.TestCase):
    def test_nomes_reais_do_documento(self):
        c = classificar_produto("GRP ARMENIA E GEORGIA MB SET 27", MAP)
        self.assertEqual((c.tipo, c.mes, c.ano, c.destino), ("Grupo", "SET", "27", "ARMENIA E GEORGIA MB"))
        c = classificar_produto("BARCO MEDITERRANEO MAI 27", MAP)
        self.assertEqual((c.tipo, c.mes, c.ano, c.destino), ("Private Expeditions", "MAI", "27", "MEDITERRANEO"))
        c = classificar_produto("FIT ADRIANA PEDRANZINI JAPAO SET 25", MAP)
        self.assertEqual((c.tipo, c.mes, c.ano), ("FIT", "SET", "25"))
        self.assertIsNone(c.destino)  # FIT: o nome traz o cliente; nunca extrair destino

    def test_trem_jato_minusculas_e_acentos(self):
        self.assertEqual(classificar_produto("trem andes jul 26", MAP).tipo, "Private Expeditions")
        self.assertEqual(classificar_produto("Jato Patagônia FEV 27", MAP).tipo, "Private Expeditions")
        self.assertEqual(classificar_produto("grp índia out 26", MAP).tipo, "Grupo")

    def test_nao_classificado(self):
        for nome in ["CRUZEIRO CARIBE JAN 27", "AEREO SAO PAULO", "", None]:
            with self.subTest(nome=nome):
                self.assertEqual(classificar_produto(nome, MAP).tipo, "Não classificado")
        c = classificar_produto("GRP PERU", MAP)
        self.assertEqual((c.mes, c.ano), (None, None))  # sem mês/ano no fim

    def test_funis(self):
        casos = {"Vendas | Grupo": "Grupo", "Pós-Venda (Grupo)": "Grupo", "Vendas | Private": "Private Expeditions",
                 "pos-venda (private)": "Private Expeditions", "Vendas | FIT": "FIT", "Vendas | Aéreo": "Aéreo",
                 "Pós-Venda (Aéreo)": "Aéreo", "Negociações 2025": "Outros", "Funil Novo": "Outros", None: "Outros",
                 "Perdas": "Outros", "Perdas | Aéreo": "Aéreo", "Vendas | Incoming": "Outros",
                 "Latitudes Preview 2027": "Outros", "Funil | Novo": "Outros"}
        for funil, tipo in casos.items():
            with self.subTest(funil=funil):
                self.assertEqual(classificar_funil(funil, MAP), tipo)

    def test_normalizacao(self):
        self.assertEqual(normalizar_cpf("123.456.789-09"), "12345678909")
        self.assertIsNone(normalizar_cpf("123"))
        self.assertEqual(normalizar_email("  Joao.Exemplo@Exemplo.COM "), "joao.exemplo@exemplo.com")


class TestContas(unittest.TestCase):
    def viagens(self, *ordens):
        return [viagem_de_registro(o, MAP) for o in ordens]

    def test_ticket_medio_por_viagem_sem_canceladas(self):
        v = self.viagens(
            ordem(1, "GRP PERU MAI 26", [("A", None), ("B", None)], valor=20000.0, inicio=(2026, 5, 1)),
            ordem(2, "FIT X JAPAO NOV 25", [("A", None)], valor=10000.0, inicio=(2025, 11, 1)),
            ordem(3, "GRP INDIA JUN 26", [("A", None)], valor=99999.0, inicio=(2026, 6, 1), status="Cancelado"),
        )
        c = calcular_viagens(v, HOJE, historico_completo=False)
        self.assertEqual(len(c["no_periodo"]), 2)
        self.assertEqual(c["canceladas"], 1)
        brl = c["por_moeda"][0]
        self.assertEqual((brl.viagens, brl.total, brl.ticket_medio), (2, 30000.0, 15000.0))
        self.assertEqual((c["single"], c["double"]), (1, 1))

    def test_periodo_12_meses_futuras_e_historico_completo(self):
        v = self.viagens(
            ordem(1, "GRP A MAI 26", [("A", None)], inicio=(2026, 5, 1)),
            ordem(2, "GRP B MAI 24", [("A", None)], inicio=(2024, 5, 1)),     # fora dos 12 meses
            ordem(3, "GRP C SET 27", [("A", None)], inicio=(2027, 9, 1)),     # futura
            ordem(4, "GRP D", [("A", None)], inicio=None, criado=(2026, 8, 1)),  # usa a data da OS
            ordem(5, "GRP E", [("A", None)], inicio=None, criado=None),       # sem data
        )
        c = calcular_viagens(v, HOJE, historico_completo=False)
        self.assertEqual(sorted(x.produto for x in c["no_periodo"]), ["GRP A MAI 26", "GRP D"])
        self.assertEqual([x.produto for x in c["futuras"]], ["GRP C SET 27"])
        self.assertEqual(len(c["sem_data"]), 1)
        self.assertEqual(c["inicio"], date(2025, 10, 7))
        self.assertEqual(next(x for x in c["no_periodo"] if x.produto == "GRP D").data_origem, "data da venda")
        completo = calcular_viagens(v, HOJE, historico_completo=True)
        self.assertEqual(len(completo["no_periodo"]), 3)
        self.assertIsNone(completo["inicio"])

    def test_data_da_viagem_pelo_nome_do_produto(self):
        # O Envision devolve startDate = dia da consulta (nível 10, 08/10/2026).
        registro = ordem(1, "GRP JAPAO LF MAI 27", [("A", None), ("B", None)], inicio=(2026, 10, 7), criado=(2026, 10, 6))
        v = viagem_de_registro(registro, MAP)
        self.assertEqual((v.data, v.data_origem), (date(2027, 5, 1), "mês da viagem"))
        c = calcular_viagens([v], HOJE, historico_completo=False)
        self.assertEqual((len(c["no_periodo"]), len(c["futuras"]), c["por_moeda"]), (0, 1, []))
        rd = RDFalso([contato("c1", "João Exemplo", "joao@exemplo.com", cpf="12345678909")])
        registro["travellers"][0]["documents"] = [{"type": "CPF", "number": "12345678909"}]
        texto = resumo_para_ia(buscar(rd, EnvisionFalso([registro])))
        self.assertIn("mês da viagem 05/2027", texto)
        self.assertIn("com data futura", texto)

    def test_moedas_diferentes_nao_somam(self):
        v = self.viagens(
            ordem(1, "GRP A MAI 26", [("A", None)], valor=10000.0, moeda="BRL"),
            ordem(2, "GRP B MAI 26", [("A", None)], valor=3000.0, moeda="USD"),
        )
        c = calcular_viagens(v, HOJE, historico_completo=False)
        self.assertEqual([(r.moeda, r.total) for r in c["por_moeda"]], [("BRL", 10000.0), ("USD", 3000.0)])

    def test_sem_valor_e_sem_pessoas(self):
        v = self.viagens(
            ordem(1, "GRP A MAI 26", [("A", None)], valor=None),
            ordem(2, "GRP B MAI 26", [], valor=5000.0),
        )
        c = calcular_viagens(v, HOJE, historico_completo=False)
        self.assertEqual(c["sem_valor"], 1)
        self.assertEqual(c["por_moeda"][0].viagens, 1)
        self.assertEqual(c["pessoas_nao_informadas"], 1)


class TestIdentificacao(unittest.TestCase):
    def test_cruza_por_cpf_com_formatacao_diferente(self):
        rd = RDFalso([contato("c1", "João Exemplo", "Joao@Exemplo.com", cpf="123.456.789-09", deals=["d1"])],
                     [deal("d1", "Vendas | Grupo")])
        env = EnvisionFalso([ordem(1, "GRP PERU MAI 26", [("João Exemplo", "12345678909"), ("Maria Exemplo", "98765432100")])])
        r = buscar(rd, env)
        self.assertEqual(r.situacao, "perfil")
        self.assertEqual(r.perfil.encontrado_em, ["RD Station CRM", "Envision"])
        self.assertEqual(r.perfil.email, "joao@exemplo.com")
        self.assertEqual(r.perfil.double, 1)
        self.assertEqual(len(r.perfil.negociacoes), 1)

    def test_homonimos_viram_candidatos(self):
        rd = RDFalso([contato("c1", "João Silva", "joao1@exemplo.com", cpf="11111111111"),
                      contato("c2", "João Silva", "joao2@exemplo.com", cpf="22222222222")])
        env = EnvisionFalso([ordem(1, "GRP A MAI 26", [("João Silva", "22222222222")])])
        r = buscar(rd, env, texto="João Silva")
        self.assertEqual(r.situacao, "candidatos")
        self.assertEqual(sorted(c.email for c in r.candidatos), ["joao1@exemplo.com", "joao2@exemplo.com"])
        self.assertEqual(env.consultas, [])  # Envision só depois da escolha (custo)
        for candidato in r.candidatos:
            self.assertNotIn("1111", candidato.chave)  # chave nunca expõe CPF
        texto = resumo_para_ia(r)
        self.assertIn("não escolha sozinha", texto)
        escolhido = buscar(rd, env, texto="João Silva", escolha=r.candidatos[1].chave)
        self.assertEqual(escolhido.situacao, "perfil")
        self.assertEqual(escolhido.perfil.email, r.candidatos[1].email)
        self.assertEqual(env.consultas, ["22222222222"])
        self.assertEqual(len(escolhido.perfil.viagens), 1)

    def test_sem_cadastro_do_formulario_no_envision(self):
        rd = RDFalso([contato("c1", "João Exemplo", "joao@exemplo.com", cpf="12345678909")])
        r = buscar(rd, EnvisionFalso())
        self.assertEqual(r.situacao, "perfil")
        self.assertEqual(r.perfil.encontrado_em, ["RD Station CRM"])
        self.assertEqual(r.perfil.nao_encontrado_em, [])  # não consultável ≠ não encontrado
        self.assertEqual(r.perfil.viagens_indisponiveis, SEM_CADASTRO_ENVISION)
        texto = resumo_para_ia(r)
        self.assertIn("NÃO CONSULTADAS", texto)
        self.assertIn("formulário de viajantes", texto)
        self.assertNotIn("Não encontrado em", texto)
        self.assertNotIn("Single", texto)  # sem Single/Double zerados

    def test_cadastro_sem_vendas(self):
        rd = RDFalso([contato("c1", "João Exemplo", "joao@exemplo.com", cpf="12345678909")])
        r = buscar(rd, EnvisionFalso(cadastros=["12345678909"]))
        self.assertEqual(r.perfil.encontrado_em, ["RD Station CRM", "Envision"])
        self.assertIsNone(r.perfil.viagens_indisponiveis)
        self.assertIn("Sem viagens com valor no período", resumo_para_ia(r))

    def test_so_no_envision_nao_e_buscado_pelo_nome(self):
        env = EnvisionFalso([ordem(1, "GRP A MAI 26", [("João Exemplo", "12345678909")])])
        r = buscar(RDFalso(), env)
        self.assertEqual(r.situacao, "nao_encontrado")
        self.assertEqual(env.consultas, [])
        self.assertIn("a busca começa pelo RD Station CRM", resumo_para_ia(r))

    def test_sem_cpf_no_rd_avisa_e_nao_consulta_o_envision(self):
        rd = RDFalso([contato("c1", "João Exemplo", "joao@exemplo.com")])
        env = EnvisionFalso([ordem(1, "GRP A MAI 26", [("João Exemplo", "12345678909")])])
        r = buscar(rd, env, mapa=MAP)  # sem campo de CPF configurado
        self.assertEqual(r.situacao, "perfil")
        self.assertEqual(r.perfil.viagens_indisponiveis, SEM_CPF_NO_RD)
        self.assertEqual(env.consultas, [])

    def test_busca_por_email(self):
        rd = RDFalso([contato("c1", "João Exemplo", "joao@exemplo.com", cpf="12345678909"), contato("c2", "Outro", "outro@exemplo.com")])
        env = EnvisionFalso([ordem(1, "GRP A MAI 26", [("João Exemplo", "12345678909")])])
        r = buscar(rd, env, texto=" JOAO@exemplo.com ")
        self.assertEqual(r.situacao, "perfil")
        self.assertEqual(r.perfil.encontrado_em, ["RD Station CRM", "Envision"])

    def test_nao_encontrado(self):
        self.assertEqual(buscar(RDFalso(), EnvisionFalso(), texto="Ninguém").situacao, "nao_encontrado")

    def test_rd_fora_do_ar(self):
        rd = RDFalso(erro=IntegracaoError("RD Station CRM", "timeout"))
        env = EnvisionFalso([ordem(1, "GRP A MAI 26", [("João Exemplo", "12345678909")])])
        r = buscar(rd, env)
        self.assertEqual(r.situacao, "erro")
        self.assertIn("RD Station CRM não respondeu", resumo_para_ia(r))
        self.assertEqual(env.consultas, [])

    def test_funil_formularios_nao_aparece(self):
        rd = RDFalso([contato("c1", "João Exemplo", "joao@exemplo.com", cpf="12345678909", deals=["d1", "d2"])],
                     [deal("d1", "Formularios", valor=0.0), deal("d2", "Perdas | Aéreo", win=False)])
        r = buscar(rd, EnvisionFalso())
        self.assertEqual([(n.funil, n.tipo, n.status) for n in r.perfil.negociacoes], [("Perdas | Aéreo", "Aéreo", "perdida")])

    def test_possivel_duplicado_na_busca_por_email(self):
        # Caso real (08/10/2026): o formulário criou outro contato (com CPF);
        # a venda ficou no contato antigo, sem CPF e com outro e-mail.
        rd = RDFalso([contato("c1", "João Exemplo", "joao@exemplo.com", cpf="12345678909", deals=["d1"], telefone="+55 11 99999-0000"),
                      contato("c2", "joão exemplo", "joao.antigo@exemplo.com", deals=["d2"], telefone="(11) 9999-0000"),
                      contato("c3", "João Exemplo", "outro@exemplo.com", telefone="21988887777")],
                     [deal("d1", "Formulários"), deal("d2", "Vendas | Grupo")])
        r = buscar(rd, EnvisionFalso(), texto="joao@exemplo.com")
        self.assertEqual(r.situacao, "perfil")
        self.assertEqual(r.perfil.negociacoes, [])  # nada é juntado
        texto = resumo_para_ia(r)
        self.assertIn("Possível cadastro duplicado no RD Station CRM", texto)
        self.assertIn("joao.antigo@exemplo.com; 1 negociação(ões)", texto)
        self.assertNotIn("outro@exemplo.com", texto)  # mesmo nome, outro telefone
        for telefone in ("99999-0000", "9999-0000", "21988887777"):
            self.assertNotIn(telefone, texto)

    def test_possivel_duplicado_entre_candidatos(self):
        rd = RDFalso([contato("c1", "Ana Souza", "a@exemplo.com", telefone="11911112222"),
                      contato("c2", "Ana Souza", "b@exemplo.com", telefone="11911112222"),
                      contato("c3", "Ana Souza", "c@exemplo.com", telefone="11933334444")])
        r = buscar(rd, EnvisionFalso(), texto="Ana Souza")
        self.assertEqual(r.situacao, "candidatos")
        self.assertEqual({c.email: c.possivel_duplicado for c in r.candidatos},
                         {"a@exemplo.com": True, "b@exemplo.com": True, "c@exemplo.com": False})
        self.assertEqual(resumo_para_ia(r).count("possível cadastro duplicado"), 2)

    def test_envision_fora_do_ar_nao_derruba_o_rd(self):
        rd = RDFalso([contato("c1", "João Exemplo", "joao@exemplo.com", cpf="12345678909", deals=["d1"])],
                     [deal("d1", "Vendas | Grupo")])
        r = buscar(rd, EnvisionFalso(erro=IntegracaoError("Envision", "timeout")))
        self.assertEqual(r.situacao, "perfil")
        self.assertEqual(len(r.perfil.negociacoes), 1)
        texto = resumo_para_ia(r)
        self.assertIn("NÃO CONSULTADAS", texto)
        self.assertIn("Envision não respondeu", texto)

    def test_conflito_de_nome(self):
        rd = RDFalso([contato("c1", "João Exemplo", "joao@exemplo.com", cpf="12345678909")])
        env = EnvisionFalso([ordem(1, "GRP A MAI 26", [("João Exemplo da Silva", "12345678909")])])
        r = buscar(rd, env)
        self.assertEqual(len(r.perfil.conflitos), 1)
        self.assertIn("sistemas discordam", resumo_para_ia(r))


class TestCamposPermitidos(unittest.TestCase):
    def test_dados_sensiveis_nunca_saem(self):
        rd = RDFalso([contato("c1", "João Exemplo", "joao@exemplo.com", cpf="123.456.789-09", deals=["d1"])], [deal("d1", "Vendas | FIT")])
        env = EnvisionFalso([ordem(1, "FIT JOAO EXEMPLO JAPAO SET 25", [("João Exemplo", "12345678909"), ("Maria Exemplo", "98765432100")], inicio=(2026, 3, 1))])
        r = buscar(rd, env)
        tudo = json.dumps(asdict(r), default=str, ensure_ascii=False) + resumo_para_ia(r)
        for sensivel in SENSIVEIS:
            with self.subTest(sensivel=sensivel):
                self.assertNotIn(sensivel, tudo)
        self.assertNotIn("Maria", tudo)  # acompanhantes não aparecem pelo nome
        self.assertIn("destino não informado", resumo_para_ia(r))  # FIT sem destino


if __name__ == "__main__":
    unittest.main()


class TestPontaAPonta(unittest.TestCase):
    """Conectores reais + respostas fictícias (tests/fixtures) + regras."""

    def test_perfil_pelos_conectores(self):
        from integracoes.envision import Envision
        from integracoes.rd_crm import RDCRM
        from tests.apoio import BASE_ENVISION_FICTICIA, CHAVE_FICTICIA, TOKEN_FICTICIO, TransporteFicticio, fixture

        rd = RDCRM(token=TOKEN_FICTICIO, transporte=TransporteFicticio({
            ("GET", "/contacts"): fixture("rd_crm/contacts_joao.json"),
            ("GET", "/deals/d1"): fixture("rd_crm/deal_d1.json"),
            ("GET", "/deals/d2"): fixture("rd_crm/deal_d1.json"),
        }))
        env = Envision(base_url=BASE_ENVISION_FICTICIA, chave=CHAVE_FICTICIA, formato_chave="pura", contexto={},
                       transporte=TransporteFicticio({
            ("POST", "/Records/Query"): fixture("envision/person_cpf.json"),
            ("GET", "/Records/123"): fixture("envision/record_123.json"),
        }))
        mapa = json.loads(json.dumps(MAP))
        mapa["rd_campos"]["contato_cpf_custom_field_id"] = "cpf_field"
        r = BuscadorPerfil(rd=rd, envision=env, mapeamentos=mapa, hoje=HOJE).buscar("João Exemplo", historico_completo=True)
        self.assertEqual(r.situacao, "perfil")
        self.assertEqual(r.perfil.encontrado_em, ["RD Station CRM", "Envision"])
        self.assertEqual(r.perfil.viagens_futuras[0].tipo, "Grupo")  # GRP ... SET 27
        texto = resumo_para_ia(r)
        for sensivel in ["123.456.789-09", "12345678909", "11999990000", "Anotação livre", "Rua Fictícia", TOKEN_FICTICIO, CHAVE_FICTICIA]:
            self.assertNotIn(sensivel, texto)


class TestCamposEnvisionReais(unittest.TestCase):
    """Formato visto no nível 2 do script (08/10/2026)."""

    def test_destino_estruturado_tem_preferencia(self):
        registro = ordem(1, "FIT JOAO EXEMPLO JAPAO SET 25", [("João Exemplo", None)])
        registro["destination"] = {"name": "Tóquio", "fullName": "Tóquio, Japão"}
        self.assertEqual(viagem_de_registro(registro, MAP).destino, "Tóquio")

    def test_sem_destino_estruturado_usa_o_nome(self):
        registro = ordem(1, "GRP PERU MAI 26", [("João Exemplo", None)])
        self.assertEqual(viagem_de_registro(registro, MAP).destino, "PERU")

    def test_cpf_com_tipo_escrito_de_outro_jeito(self):
        # O nome do Envision vem do viajante com o CPF (tipo "Cpf ", com pontos).
        registro = ordem(1, "GRP PERU MAI 26", [])
        registro["travellers"] = [{"fullName": "Ana Souza Lima", "documents": [{"type": "Cpf ", "value": "111.111.111-11"}]}]
        env = EnvisionFalso([registro])
        r = buscar(RDFalso([contato("c1", "Ana Souza", "ana@exemplo.com", cpf="11111111111")]), env, texto="Ana Souza")
        self.assertEqual(r.situacao, "perfil")
        self.assertEqual(r.perfil.encontrado_em, ["RD Station CRM", "Envision"])
        self.assertEqual(len(r.perfil.conflitos), 1)

    def test_venda_com_itens_filhos(self):
        """ServiceOrder do nível 10: produto no ItineraryBook, destino nos serviços."""
        registro = ordem(1, "OS sem padrão", [("A", None), ("B", None)])
        registro["children"] = [
            {"type": "ItineraryBook", "summary": "GRP JAPAO LF MAI 27"},
            {"type": "ServiceBook", "summary": "Aéreo", "destination": {"name": "Tóquio"}},
            {"type": "ServiceBook", "summary": "Hotel", "destination": {"name": "Quioto"}},
            {"type": "ServiceBook", "summary": "Hotel", "destination": {"name": "Quioto"}},
        ]
        v = viagem_de_registro(registro, MAP)
        self.assertEqual((v.produto, v.tipo, v.destino, v.categoria), ("GRP JAPAO LF MAI 27", "Grupo", "Quioto", "Double"))
        registro["children"] = [{"type": "ItineraryBook", "summary": "GRP JAPAO LF MAI 27"}]
        self.assertEqual(viagem_de_registro(registro, MAP).destino, "JAPAO LF")  # reserva: nome do produto

