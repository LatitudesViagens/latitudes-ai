"""Camada 3: ferramenta perfil_cliente do agente, com dados fictícios."""

import json
import unittest
from unittest import mock

import agent_tools.perfil_cliente as ferramenta
from services.contexto_turno import TURNO_ATUAL, TurnoAtual
from services.perfil_cliente import carregar_mapeamentos
from tests.apoio import bloquear_internet
from tests.test_perfil_cliente import EnvisionFalso, RDFalso, contato, deal, ordem

bloquear_internet()


class BancoFalso:
    """Registra os inserts (como o Supabase) para conferir o registro."""

    def __init__(self, falhar=False):
        self.inserts, self.falhar = [], falhar

    def table(self, nome):
        banco = self

        class Tabela:
            def insert(self, registro):
                banco.inserts.append((nome, registro))
                return self

            def execute(self):
                if banco.falhar:
                    raise RuntimeError("banco fora do ar")

        return Tabela()


MAPA = json.loads(json.dumps(carregar_mapeamentos()))
MAPA["rd_campos"]["contato_cpf_custom_field_id"] = "cf_cpf"


def chamar(rd, env, banco=None, avisos=(), **kwargs):
    """Chama a ferramenta e grava as consultas como o chat_service faz no fim
    do turno."""
    from services.chat_service import _save_client_lookups

    banco = banco or BancoFalso()
    turno = TurnoAtual(user_id="u1", conversation_id="conv1")
    token = TURNO_ATUAL.set(turno)
    try:
        with mock.patch.object(ferramenta, "_criar_conectores", return_value=(rd, env, list(avisos))), \
             mock.patch.object(ferramenta, "carregar_mapeamentos", return_value=MAPA):
            saida = ferramenta.perfil_cliente(**kwargs)
    finally:
        TURNO_ATUAL.reset(token)
    _save_client_lookups(client=banco, turno=turno)
    return saida, banco


RD_JOAO = RDFalso([contato("c1", "João Exemplo", "joao@exemplo.com", cpf="12345678909", deals=["d1"])], [deal("d1", "Vendas | Private")])
ENV_JOAO = EnvisionFalso([ordem(1, "BARCO MEDITERRANEO MAI 26", [("João Exemplo", "12345678909"), ("Ana", None)], inicio=(2026, 5, 1))])


class TestFerramenta(unittest.TestCase):
    def test_perfil_com_registro_sem_cpf(self):
        saida, banco = chamar(RD_JOAO, ENV_JOAO, cliente="João Exemplo")
        self.assertEqual(saida["status"], "perfil")
        self.assertIn("Private Expeditions", saida["resumo"])
        self.assertIn("fonte: Envision", saida["resumo"])
        tabela, registro = banco.inserts[0]
        self.assertEqual(tabela, "client_lookups")
        self.assertEqual(registro["outcome"], "encontrado")
        self.assertEqual(registro["conversation_id"], "conv1")
        self.assertEqual(registro["client_email"], "joao@exemplo.com")
        self.assertEqual(registro["systems"], ["RD Station CRM", "Envision"])
        self.assertNotIn("12345678909", json.dumps(registro) + saida["resumo"])
        self.assertNotIn("user_id", registro)  # quem consultou vem do login (auth.uid())

    def test_candidatos_e_escolha(self):
        rd = RDFalso([contato("c1", "João Silva", "a@exemplo.com"), contato("c2", "João Silva", "b@exemplo.com")])
        saida, banco = chamar(rd, EnvisionFalso(), cliente="João Silva")
        self.assertEqual(saida["status"], "candidatos")
        self.assertEqual(banco.inserts[0][1]["outcome"], "candidatos")
        chave = saida["resumo"].split("[escolha: ")[1].split("]")[0]
        saida2, banco2 = chamar(rd, EnvisionFalso(), cliente="João Silva", escolha=chave)
        self.assertEqual(saida2["status"], "perfil")
        self.assertEqual(banco2.inserts[0][1]["outcome"], "encontrado")

    def test_aviso_para_dados_fora_da_lista(self):
        saida, _ = chamar(RD_JOAO, ENV_JOAO, cliente="João Exemplo")
        texto = saida["resumo"]
        self.assertIn("Esse nível de informação não está autorizado pela ÁGORA. Para consultar histórico de saúde, acesse o RD Station CRM.", texto)
        self.assertIn("Para consultar formulário do viajante, acesse o RD Station CRM.", texto)
        self.assertIn("Para consultar pagamentos, acesse o Envision.", texto)

    def test_sistema_sem_credencial_avisa(self):
        # A busca começa pelo RD: sem ele, não há como achar o cliente.
        saida, banco = chamar(None, ENV_JOAO, avisos=["Credencial ausente ou inválida no RD Station CRM."], cliente="João Exemplo")
        self.assertEqual(saida["status"], "erro")
        self.assertIn("Credencial ausente ou inválida no RD Station CRM", saida["resumo"])
        self.assertEqual(banco.inserts[0][1]["outcome"], "erro")
        self.assertEqual(banco.inserts[0][1]["systems"], [])

    def test_envision_sem_credencial_mostra_o_rd(self):
        saida, banco = chamar(RD_JOAO, None, avisos=["Credencial ausente ou inválida no Envision."], cliente="João Exemplo")
        self.assertEqual(saida["status"], "perfil")
        self.assertIn("NÃO CONSULTADAS", saida["resumo"])
        self.assertEqual(banco.inserts[0][1]["systems"], ["RD Station CRM"])

    def test_falha_no_registro_nao_impede_a_resposta(self):
        saida, _ = chamar(RD_JOAO, ENV_JOAO, banco=BancoFalso(falhar=True), cliente="João Exemplo")
        self.assertEqual(saida["status"], "perfil")

    def test_nao_encontrado_registrado(self):
        saida, banco = chamar(RDFalso(), EnvisionFalso(), cliente="Ninguém")
        self.assertEqual(saida["status"], "nao_encontrado")
        self.assertEqual(banco.inserts[0][1]["outcome"], "nao_encontrado")

    def test_sem_credenciais_reais_o_conector_nao_e_criado(self):
        with mock.patch.dict("os.environ", {"RD_CRM_API_TOKEN": "", "ENVISION_BASE_URL": "", "ENVISION_API_KEY": ""}):
            rd, env, avisos = ferramenta._criar_conectores("u1")
        self.assertIsNone(rd)
        self.assertIsNone(env)
        self.assertEqual(len(avisos), 2)


class TestAgente(unittest.TestCase):
    def test_ferramenta_registrada_e_regras_no_prompt(self):
        import truststore
        truststore.inject_into_ssl()
        from latitudes_agent.agent import fallback_agent, root_agent

        for agente in (root_agent, fallback_agent):
            self.assertIn("perfil_cliente", [t.__name__ for t in agente.tools])
        instrucao = root_agent.instruction
        self.assertIn("nunca escolha", instrucao)
        self.assertIn("não são autorizados pela ÁGORA", instrucao)


if __name__ == "__main__":
    unittest.main()
