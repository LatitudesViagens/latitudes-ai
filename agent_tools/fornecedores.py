"""Ferramenta do agente: fornecedores da Latitudes (migração 015).

Só para a equipe de Operações e o TI: quem pode é decidido pelo turno, no
banco (services/contexto_turno.TURNO_ATUAL), nunca por algo que a IA
preencha. Para os demais, a ferramenta não devolve nada e a ÁGORA não cita
que o cadastro existe. Só consulta a lista que o turno já deixou na memória
(services/fornecedores.py); não acessa o banco nem a internet.
"""

from services.contexto_turno import TURNO_ATUAL
from services.fornecedores import describe_supplier, loaded_suppliers, search_suppliers

# Para quem não é de Operações: segue como se o cadastro não existisse.
NO_ACCESS_RESULT = {
    "status": "sem_acesso",
    "mensagem": (
        "Não há fornecedores internos disponíveis para esta pessoa. Não "
        "mencione cadastro, base, lista interna ou fornecedores da "
        "Latitudes, nem que fez esta consulta. Responda normalmente: se a "
        "pessoa pediu fornecedores, pesquise na internet (search_web) e "
        "apresente as opções apenas como encontradas na internet, sem "
        "falar em homologação, aprovação ou lista da Latitudes."
    ),
}


def buscar_fornecedores(
    destino: str,
    servico: str = "",
    perfil: str = "",
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
        perfil: só se a pessoa pedir um perfil: "Luxo", "Superior",
            "Intermediário" ou "Econômico" (grupos mais simples); vazio
            traz todos os perfis.

    Returns:
        A lista dos fornecedores da Latitudes encontrados, ou o aviso de que
        não há nenhum cadastrado para o pedido.
    """
    turno = TURNO_ATUAL.get()

    if turno is None or not turno.can_read_suppliers:
        return dict(NO_ACCESS_RESULT)

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
        profile=perfil,
    )

    if not found:
        return {
            "status": "nenhum",
            "mensagem": (
                "Nenhum fornecedor da Latitudes cadastrado para este destino "
                "e serviço. Diga isso em uma frase e ofereça buscar "
                "opções na internet. Não pesquise sem a pessoa aceitar; se "
                "pesquisar, marque o resultado como 'encontrados na "
                "internet, não homologados pela Latitudes'."
            ),
        }

    lines = [describe_supplier(supplier) for supplier in found]

    if total > len(found):
        lines.append(f"(mais {total - len(found)} não listados; peça um serviço ou cidade para filtrar)")

    return {
        "status": "ok",
        "fornecedores": "\n".join(lines),
        "mensagem": (
            "Apresente estes numa seção 'Fornecedores da Latitudes', como "
            "sugestão no chat (não gere arquivo). Use só os dados listados; "
            "não invente contato, estrelas nem serviços. Termine oferecendo "
            "buscar na internet outros fornecedores do local; só pesquise "
            "se a pessoa pedir ou aceitar e, nesse caso, apresente o "
            "resultado em seção separada, marcado como 'encontrados na "
            "internet, não homologados pela Latitudes'."
        ),
    }
