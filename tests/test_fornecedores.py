"""Fornecedores da base de conhecimento: busca, leitura das fichas e travas
(dados fictícios; nada chama a IA nem o banco)."""

import unittest
from unittest import mock

from database.suppliers import _record, clean_stars
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
from ui.sugerir_fornecedores import (
    asked_to_suggest,
    incomplete_rows,
    is_included,
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
    {"name": "Hotel Nilo Azul", "city": "Cairo", "country": "Egito", "stars": 4, "service": "Hotel", "contact": "nilo.example"},
    {"name": "Palácio do Deserto", "city": "Luxor", "country": "Egito", "stars": 5, "service": "Hospedagem", "contact": None},
    {"name": "Faraó Receptivo", "city": "", "country": "Egito", "stars": None, "service": "Receptivo (DMC)", "contact": None},
    {"name": "Lisboa Tours", "city": "Lisboa", "country": "Portugal", "stars": None, "service": "Guia em português", "contact": None},
    {"name": "Big Apple Bus", "city": "Nova York", "country": "Estados Unidos", "stars": None, "service": "Transporte com ônibus", "contact": None},
)


class BuscaTest(unittest.TestCase):
    def nomes(self, destino: str, servico: str = "") -> list[str]:
        encontrados, _total = search_suppliers(LISTA, destination=destino, service=servico)
        return [item["name"] for item in encontrados]

    def test_pais_traz_todos_do_pais_com_mais_estrelas_antes(self):
        self.assertEqual(
            self.nomes("Egito"),
            ["Palácio do Deserto", "Hotel Nilo Azul", "Faraó Receptivo"],
        )

    def test_cidade_vem_antes_do_resto_do_pais(self):
        self.assertEqual(self.nomes("Cairo, Egito")[0], "Hotel Nilo Azul")

    def test_sem_acento_e_minusculas(self):
        self.assertEqual(self.nomes("egito", "RECEPTIVO"), ["Faraó Receptivo"])

    def test_sinonimos_de_servico(self):
        self.assertEqual(
            self.nomes("Egito", "hospedagem"),
            ["Palácio do Deserto", "Hotel Nilo Azul"],
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
            '"pais": "Egito", "estrelas": "4", "servico": "Hotel", "contato": ""}, '
            '{"nome": "", "pais": "Egito"}]}'
        )
        self.assertEqual(
            _parse_suppliers(texto),
            [{"name": "Hotel Nilo", "city": "Cairo", "country": "Egito", "stars": 4, "service": "Hotel", "contact": ""}],
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

    def test_celula_vazia_da_tabela(self):
        self.assertEqual(
            supplier_key({"name": "Faraó", "city": float("nan"), "country": "Egito"}),
            supplier_key({"name": "farao", "city": None, "country": "EGITO"}),
        )


class FichaTest(unittest.TestCase):
    def test_estrelas(self):
        self.assertEqual(clean_stars(4.0), 4)
        self.assertEqual(clean_stars("5*"), 5)
        self.assertIsNone(clean_stars(float("nan")))
        self.assertIsNone(clean_stars(""))
        self.assertIsNone(clean_stars(7))

    def test_registro_so_com_campos_da_ficha(self):
        registro = _record(
            {"name": "X", "city": float("nan"), "country": "Egito", "stars": 3.0, "service": "Hotel", "contact": "", "include": True, "preco": 100}
        )
        self.assertEqual(
            registro,
            {"name": "X", "city": None, "country": "Egito", "stars": 3, "service": "Hotel", "contact": None},
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


class PedidoSemArquivoTest(unittest.TestCase):
    def test_reconhece_pedidos_de_sugestao(self):
        for frase in (
            "Quero sugerir esse fornecedor para a base de conhecimento",
            "Gostaria de cadastrar o Hotel Nilo, no Cairo",
            "Pode incluir na base o receptivo Faraó?",
            "Sugestão de fornecedor: Lisboa Tours, guias",
            "quero adicionar dois hotéis do Egito",
        ):
            self.assertTrue(asked_to_suggest(frase), frase)

    def test_nao_confunde_com_pedido_de_fornecedores(self):
        for frase in (
            "Quais os principais fornecedores para o Egito?",
            "Me indique hotéis em Lisboa",
            "Monte um roteiro de 5 dias no Cairo",
        ):
            self.assertFalse(asked_to_suggest(frase), frase)

    def test_resposta_que_aponta_o_botao(self):
        self.assertTrue(
            asked_to_suggest(
                "tenho um contato bom no Cairo",
                answer="Use o botão 'Sugerir fornecedor para a base' abaixo.",
            )
        )

    def test_linhas_incluidas(self):
        self.assertTrue(is_included({"include": True, "name": ""}))
        self.assertFalse(is_included({"include": False, "name": "Hotel"}))
        # Linha nova pelo "+": sem marca, entra se tiver nome.
        self.assertTrue(is_included({"include": None, "name": "Hotel"}))
        self.assertFalse(is_included({"include": None, "name": float("nan")}))


class FerramentaTest(unittest.TestCase):
    def test_sem_lista_carregada_avisa_indisponivel(self):
        from agent_tools.fornecedores import buscar_fornecedores

        with mock.patch.object(fornecedores, "_cache", None):
            self.assertEqual(buscar_fornecedores("Egito")["status"], "indisponivel")

    def test_devolve_os_da_latitudes(self):
        from agent_tools.fornecedores import buscar_fornecedores

        with mock.patch.object(fornecedores, "_cache", (0.0, LISTA)):
            resultado = buscar_fornecedores("Egito", "hotel")

        self.assertEqual(resultado["status"], "ok")
        self.assertIn("Palácio do Deserto — Hospedagem — Luxor, Egito — 5 estrelas", resultado["fornecedores"])
        self.assertIn("contato: nilo.example", resultado["fornecedores"])

    def test_nenhum_encontrado(self):
        from agent_tools.fornecedores import buscar_fornecedores

        with mock.patch.object(fornecedores, "_cache", (0.0, LISTA)):
            self.assertEqual(buscar_fornecedores("Japão")["status"], "nenhum")


if __name__ == "__main__":
    unittest.main()
