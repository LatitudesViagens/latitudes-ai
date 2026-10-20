"""Ferramenta do agente: fornecedores da Latitudes (base de conhecimento).

Só consulta a lista de aprovados que o turno já deixou na memória
(services/fornecedores.py); não acessa o banco nem a internet.
"""

from services.fornecedores import describe_supplier, loaded_suppliers, search_suppliers


def buscar_fornecedores(
    destino: str,
    servico: str = "",
) -> dict:
    """Consulta os fornecedores da Latitudes aprovados na base de
    conhecimento (hotéis, receptivos, guias, transporte, restaurantes,
    atrativos, visitas técnicas etc.).

    Use SEMPRE antes de pesquisar na internet quando a pessoa pedir ou
    precisar de fornecedores, hotéis, receptivos, guias, transporte ou
    prestadores de serviço para um destino, inclusive ao montar roteiros.

    Args:
        destino: cidade e/ou país em português, por exemplo "Cairo, Egito",
            "Egito" ou "Lisboa e Porto".
        servico: tipo de serviço, se a pessoa disser (ex.: "hotel",
            "receptivo", "guia", "transporte"); vazio traz todos.

    Returns:
        A lista dos fornecedores da Latitudes encontrados, ou o aviso de que
        não há nenhum cadastrado para o pedido.
    """
    suppliers = loaded_suppliers()

    if suppliers is None:
        return {
            "status": "indisponivel",
            "mensagem": (
                "A lista de fornecedores da Latitudes não está disponível "
                "agora. Diga isso em uma frase e, se for útil, siga com "
                "sugestões da internet marcadas como não homologadas."
            ),
        }

    found, total = search_suppliers(
        suppliers=suppliers,
        destination=destino,
        service=servico,
    )

    if not found:
        return {
            "status": "nenhum",
            "mensagem": (
                "Nenhum fornecedor da Latitudes cadastrado para este destino "
                "e serviço. Diga isso em uma frase; se for útil, sugira "
                "opções da internet marcadas como 'encontradas na internet, "
                "não homologadas pela Latitudes'."
            ),
        }

    lines = [describe_supplier(supplier) for supplier in found]

    if total > len(found):
        lines.append(f"(mais {total - len(found)} não listados; peça um serviço ou cidade para filtrar)")

    return {
        "status": "ok",
        "fornecedores": "\n".join(lines),
        "mensagem": (
            "Apresente estes primeiro, numa seção 'Fornecedores da "
            "Latitudes', como sugestão no chat (não gere arquivo). Use só os "
            "dados listados; não invente contato, estrelas nem serviços. "
            "Sugestões da internet, se houver, vêm depois e separadas, "
            "marcadas como 'encontradas na internet, não homologadas pela "
            "Latitudes'."
        ),
    }
