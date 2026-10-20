"""Fornecedores (só equipe de Operações e TI): busca, leitura das fichas,
travas e acesso (dados fictícios; nada chama a IA nem o banco)."""

import unittest
from unittest import mock

from database.suppliers import _record, clean_profile
from services import fornecedores
from services.fornecedores import (
    _destination_parts,
    _parse_suppliers,
    normalize,
    search_suppliers,
    split_duplicates,
    supplier_key,
)
from tests.apoio import bloquear_internet
from admin.fornecedores import (
    duplicate_messages,
    incomplete_rows,
    is_included,
    supplier_frame,
    text_for_privacy_check,
)

bloquear_internet()


def _carregados(*linhas: dict) -> list[dict]:
    fornecedores_ = []

    for linha in linhas:
        item = dict(linha)
        item["_name"] = normalize(linha.get("name"))
        item["_city"] = normalize(linha.get("city"))
        item["_country"] = normalize(linha.get("country"))
        item["_service"] = normalize(linha.get("service"))
        fornecedores_.append(item)

    return fornecedores_


LISTA = _carregados(
    {"name": "Hotel Nilo Azul", "city": "Cairo", "country": "Egito", "profile": "BUDGET", "service": "Hotel", "contact": "nilo.example"},
    {"name": "Palácio do Deserto", "city": "Luxor", "country": "Egito", "profile": "VIP", "service": "Hospedagem", "contact": None},
    {"name": "Faraó Receptivo", "city": "", "country": "Egito", "profile": "SERVIÇOS SEPARADOS", "service": "Receptivo (DMC)", "contact": None},
    {"name": "Lisboa Tours", "city": "Lisboa", "country": "Portugal", "service": "Guia em português", "contact": None},
    {"name": "Big Apple Bus", "city": "Nova York", "country": "Estados Unidos", "service": "Transporte com ônibus", "contact": None},
)


class BuscaTest(unittest.TestCase):
    def nomes(self, destino: str, servico: str = "") -> list[str]:
        encontrados, _total = search_suppliers(LISTA, destination=destino, service=servico)
        return [item["name"] for item in encontrados]

    def test_pais_traz_todos_do_pais_por_nome(self):
        self.assertEqual(
            self.nomes("Egito"),
            ["Faraó Receptivo", "Hotel Nilo Azul", "Palácio do Deserto"],
        )

    def test_cidade_vem_antes_do_resto_do_pais(self):
        self.assertEqual(self.nomes("Cairo, Egito")[0], "Hotel Nilo Azul")

    def test_sem_acento_e_minusculas(self):
        self.assertEqual(self.nomes("egito", "RECEPTIVO"), ["Faraó Receptivo"])

    def test_sinonimos_de_servico(self):
        self.assertEqual(
            self.nomes("Egito", "hospedagem"),
            ["Hotel Nilo Azul", "Palácio do Deserto"],
        )
        self.assertEqual(self.nomes("Egito", "dmc"), ["Faraó Receptivo"])
        self.assertEqual(self.nomes("Nova York", "traslado"), ["Big Apple Bus"])

    def test_apelido_de_destino(self):
        self.assertEqual(self.nomes("EUA"), ["Big Apple Bus"])

    def test_varios_destinos(self):
        self.assertEqual(self.nomes("Lisboa e Cairo"), ["Hotel Nilo Azul", "Lisboa Tours"])

    def test_destino_sem_fornecedor(self):
        self.assertEqual(self.nomes("Japão"), [])

    def test_partes_do_destino(self):
        self.assertEqual(_destination_parts("Cairo, Egito"), ["cairo", "egito"])
        self.assertEqual(_destination_parts("São Paulo ou Rio"), ["sao paulo", "rio"])


class LeituraDaIATest(unittest.TestCase):
    def test_le_o_json_e_limpa_os_campos(self):
        texto = (
            'Aqui está: {"fornecedores": [{"nome": "  Hotel  Nilo ", "cidade": "Cairo", '
            '"pais": "Egito", "perfil": "premium", "servico": "Hotel", "contato": ""}, '
            '{"nome": "", "pais": "Egito"}]}'
        )
        self.assertEqual(
            _parse_suppliers(texto),
            [{"name": "Hotel Nilo", "city": "Cairo", "country": "Egito", "profile": "VIP", "service": "Hotel", "contact": ""}],
        )

    def test_resposta_sem_json(self):
        self.assertIsNone(_parse_suppliers("não consegui"))

    def test_mesma_ficha_reconhecida_sem_acento(self):
        self.assertEqual(
            supplier_key({"name": "Palácio", "city": "Luxor", "country": "Egito"}),
            supplier_key({"name": "palacio", "city": "LUXOR", "country": "egito"}),
        )


class DuplicidadeTest(unittest.TestCase):
    def test_ja_na_base_e_repetido_na_lista(self):
        na_base = {supplier_key({"name": "Hotel Nilo Azul", "city": "Cairo", "country": "Egito"})}
        novos, repetidos = split_duplicates(
            rows=[
                {"name": "HOTEL NILO AZUL", "city": "cairo", "country": "Egito"},
                {"name": "Palácio do Deserto", "city": "Luxor", "country": "Egito"},
                {"name": "Palacio do Deserto", "city": "Luxor", "country": "egito"},
                {"name": "Palácio do Deserto", "city": "Assuã", "country": "Egito"},
            ],
            existing_keys=na_base,
        )
        self.assertEqual(
            [(item["name"], item["city"]) for item in novos],
            [("Palácio do Deserto", "Luxor"), ("Palácio do Deserto", "Assuã")],
        )
        self.assertEqual(len(repetidos), 2)

    def test_avisos_do_cadastro(self):
        cadastrados = {supplier_key({"name": "Hotel Nilo Azul", "city": "Cairo", "country": "Egito"})}
        avisos = duplicate_messages(
            rows=[
                {"name": "hotel nilo azul", "city": "CAIRO", "country": "Egito"},
                {"name": "Hotel Novo", "city": "Luxor", "country": "Egito"},
                {"name": "Hotel Novo", "city": "Luxor", "country": "Egito"},
            ],
            existing_keys=cadastrados,
        )
        self.assertEqual(
            avisos,
            [
                "hotel nilo azul (CAIRO, Egito) já está cadastrado.",
                "Hotel Novo (Luxor, Egito) aparece mais de uma vez.",
            ],
        )

    def test_celula_vazia_da_tabela(self):
        self.assertEqual(
            supplier_key({"name": "Faraó", "city": float("nan"), "country": "Egito"}),
            supplier_key({"name": "farao", "city": None, "country": "EGITO"}),
        )


class PerfilTest(unittest.TestCase):
    def test_nomes_e_sinonimos(self):
        self.assertEqual(clean_profile("vip"), "VIP")
        self.assertEqual(clean_profile("Premium / boutique"), "VIP")
        self.assertEqual(clean_profile("alto padrão"), "VIP")
        self.assertEqual(clean_profile("budget"), "BUDGET")
        self.assertEqual(clean_profile("grupos mais simples"), "BUDGET")
        self.assertEqual(clean_profile("ECONÔMICO"), "BUDGET")
        self.assertEqual(clean_profile("Serviços separados"), "SERVIÇOS SEPARADOS")
        self.assertEqual(clean_profile("venda avulsa"), "SERVIÇOS SEPARADOS")
        # Modelo de venda vale mesmo se o texto falar em luxo.
        self.assertEqual(clean_profile("luxo, serviços separados"), "SERVIÇOS SEPARADOS")
        self.assertIsNone(clean_profile("Não informado"))
        self.assertIsNone(clean_profile(""))
        self.assertIsNone(clean_profile(float("nan")))

    def test_filtro_por_perfil(self):
        encontrados, _total = search_suppliers(LISTA, destination="Egito", profile="vip")
        self.assertEqual([item["name"] for item in encontrados], ["Palácio do Deserto"])
        # Perfil que não é da lista não filtra (texto solto da IA).
        encontrados, _total = search_suppliers(LISTA, destination="Egito", profile="qualquer")
        self.assertEqual(len(encontrados), 3)


class FichaTest(unittest.TestCase):
    def test_registro_so_com_campos_da_ficha(self):
        registro = _record(
            {"name": "X", "city": float("nan"), "country": "Egito", "stars": 3.0, "service": "Hotel", "contact": "", "include": True, "preco": 100}
        )
        self.assertEqual(
            registro,
            # Estrelas saíram da ficha (016): não são mais gravadas.
            {"name": "X", "city": None, "country": "Egito", "profile": None, "service": "Hotel", "contact": None},
        )

    def test_linhas_incompletas(self):
        self.assertEqual(
            incomplete_rows(
                [
                    {"name": "A", "country": "Egito", "service": "Hotel"},
                    {"name": "B", "country": "", "service": "Hotel"},
                    {"name": "C", "country": "Egito", "service": None},
                ]
            ),
            2,
        )

    def test_trava_lgpd_nao_recebe_o_contato(self):
        texto = text_for_privacy_check(
            [{"name": "Hotel", "city": "Cairo", "country": "Egito", "service": "Hotel", "contact": "reservas@hotel.example"}]
        )
        self.assertNotIn("reservas@", texto)
        self.assertIn("não clientes", texto)


class TabelaTest(unittest.TestCase):
    def test_ficha_em_branco_tem_colunas_de_texto(self):
        # Coluna toda vazia virava número (NaN) e a tabela de edição dava
        # StreamlitAPIException ao abrir a ficha em branco (20/10/2026).
        tabela = supplier_frame(
            records=[{"include": True}],
            columns=["include", "name", "city", "country", "profile", "service", "contact", "already"],
        )

        for coluna in ("name", "city", "country", "profile", "service", "contact", "already"):
            self.assertEqual(tabela[coluna].dtype, object, coluna)
            self.assertEqual(tabela[coluna].iloc[0], "")

        self.assertTrue(bool(tabela["include"].iloc[0]))

    def test_linhas_incluidas(self):
        self.assertTrue(is_included({"include": True, "name": ""}))
        self.assertFalse(is_included({"include": False, "name": "Hotel"}))
        # Linha nova pelo "+": sem marca, entra se tiver nome.
        self.assertTrue(is_included({"include": None, "name": "Hotel"}))
        self.assertFalse(is_included({"include": None, "name": float("nan")}))


def _turno(pode_ver: bool):
    """Turno de quem pergunta: o acesso vem do banco, nunca da IA."""
    from services.contexto_turno import TURNO_ATUAL, TurnoAtual

    return TURNO_ATUAL.set(
        TurnoAtual(
            user_id="pessoa",
            conversation_id="conversa",
            can_read_suppliers=pode_ver,
        )
    )


class FerramentaTest(unittest.TestCase):
    def tearDown(self):
        from services.contexto_turno import TURNO_ATUAL

        TURNO_ATUAL.set(None)

    def test_sem_lista_carregada_avisa_indisponivel(self):
        from agent_tools.fornecedores import buscar_fornecedores

        _turno(pode_ver=True)

        with mock.patch.object(fornecedores, "_cache", None):
            self.assertEqual(buscar_fornecedores("Egito")["status"], "indisponivel")

    def test_operacoes_recebe_os_da_latitudes(self):
        from agent_tools.fornecedores import buscar_fornecedores

        _turno(pode_ver=True)

        with mock.patch.object(fornecedores, "_cache", (0.0, LISTA)):
            resultado = buscar_fornecedores("Egito", "hotel")

        self.assertEqual(resultado["status"], "ok")
        # Um dado por linha, com o nome em negrito.
        self.assertIn(
            "**Palácio do Deserto**\n- Serviço: Hospedagem\n- Local: Luxor, Egito\n- Perfil: VIP",
            resultado["fornecedores"],
        )
        self.assertIn("- Contato: nilo.example", resultado["fornecedores"])
        # A resposta fica marcada: a tela não oferece "Sugerir para a base".
        from services.contexto_turno import TURNO_ATUAL

        self.assertTrue(TURNO_ATUAL.get().used_suppliers)

    def test_nenhum_encontrado(self):
        from agent_tools.fornecedores import buscar_fornecedores

        _turno(pode_ver=True)

        with mock.patch.object(fornecedores, "_cache", (0.0, LISTA)):
            self.assertEqual(buscar_fornecedores("Japão")["status"], "nenhum")


class AcessoTest(unittest.TestCase):
    """Quem não é de Operações (nem TI) não recebe nada, mesmo com a lista
    na memória do servidor (carregada por outra pessoa)."""

    def tearDown(self):
        from services.contexto_turno import TURNO_ATUAL

        TURNO_ATUAL.set(None)

    def test_fora_de_operacoes_nao_recebe_fornecedores(self):
        from agent_tools.fornecedores import buscar_fornecedores

        _turno(pode_ver=False)

        with mock.patch.object(fornecedores, "_cache", (0.0, LISTA)):
            resultado = buscar_fornecedores("Egito", "hotel", "VIP")

        self.assertEqual(resultado["status"], "sem_acesso")
        self.assertNotIn("fornecedores", resultado)
        from services.contexto_turno import TURNO_ATUAL

        self.assertFalse(TURNO_ATUAL.get().used_suppliers)
        self.assertNotIn("Palácio", str(resultado))
        self.assertIn("Não mencione cadastro", resultado["mensagem"])

    def test_sem_turno_nao_recebe_fornecedores(self):
        from agent_tools.fornecedores import buscar_fornecedores

        with mock.patch.object(fornecedores, "_cache", (0.0, LISTA)):
            self.assertEqual(buscar_fornecedores("Egito")["status"], "sem_acesso")

    def test_acesso_conferido_uma_vez_a_cada_5_minutos(self):
        chamadas = []

        def conferir(client):
            chamadas.append(client)
            return True

        with (
            mock.patch("database.roles.can_read_suppliers", conferir),
            mock.patch.object(fornecedores, "_access_cache", {}),
        ):
            self.assertTrue(fornecedores.user_can_read_suppliers(client="c", user_id="u1"))
            self.assertTrue(fornecedores.user_can_read_suppliers(client="c", user_id="u1"))
            self.assertTrue(fornecedores.user_can_read_suppliers(client="c", user_id="u2"))

        self.assertEqual(len(chamadas), 2)


if __name__ == "__main__":
    unittest.main()
