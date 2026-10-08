"""Ferramenta do agente: perfil do cliente (RD Station CRM + Envision).

Somente leitura. As contas e o filtro dos campos permitidos ficam em
services/perfil_cliente.py; aqui a ferramenta busca, registra a consulta e
devolve o resumo pronto para a IA escrever a resposta.
"""

from integracoes.base import pode_consultar
from integracoes.erros import IntegracaoError
from services.contexto_turno import TURNO_ATUAL
from services.perfil_cliente import (
    ENVISION,
    RD,
    BuscadorPerfil,
    carregar_mapeamentos,
    resumo_para_ia,
)

SITUACAO_PARA_REGISTRO = {
    "perfil": "encontrado",
    "candidatos": "candidatos",
    "nao_encontrado": "nao_encontrado",
    "erro": "erro",
}


def _criar_conectores(user_id: str | None) -> tuple[object | None, object | None, list[str]]:
    """Conectores com as credenciais do servidor. Sem credencial ou sem
    permissão, o sistema fica de fora e a resposta avisa."""
    from integracoes.envision import Envision
    from integracoes.rd_crm import RDCRM

    conectores: dict[str, object | None] = {}
    avisos: list[str] = []

    for sistema, fabrica in ((RD, RDCRM), (ENVISION, Envision)):
        if not pode_consultar(user_id, sistema):
            conectores[sistema] = None
            avisos.append(f"Você não tem acesso ao {sistema} pela ÁGORA.")
            continue

        try:
            conectores[sistema] = fabrica()
        except IntegracaoError as erro:
            conectores[sistema] = None
            avisos.append(str(erro))

    return conectores[RD], conectores[ENVISION], avisos


def _aviso_dados_fora_da_lista(mapeamentos: dict) -> str:
    modelo = mapeamentos["aviso_fora_da_lista"]
    linhas = [
        "",
        "DADOS QUE A ÁGORA NÃO MOSTRA: se a consultora pediu algum destes, "
        "não informe nem deduza; responda a parte permitida e use o aviso:",
    ]

    for dado, sistema in mapeamentos["dados_fora_da_lista"].items():
        linhas.append(f"- {dado}: \"{modelo.format(dado=dado, sistema=sistema)}\"")

    return "\n".join(linhas)


def _registrar(texto: str, resultado) -> None:
    """Anota a consulta no turno; o chat_service grava em client_lookups."""
    turno = TURNO_ATUAL.get()

    if turno is None:
        return

    perfil = resultado.perfil
    sistemas = perfil.encontrado_em if perfil else sorted(
        {sistema for candidato in resultado.candidatos for sistema in candidato.sistemas}
    )
    turno.consultas_cliente.append(
        {
            "search_text": texto,
            "outcome": SITUACAO_PARA_REGISTRO[resultado.situacao],
            "systems": sistemas,
            "client_name": perfil.nome if perfil else None,
            "client_email": perfil.email if perfil else None,
        }
    )


def perfil_cliente(
    cliente: str,
    historico_completo: bool = False,
    escolha: str = "",
) -> dict:
    """Consulta o perfil de um cliente da Latitudes no RD Station CRM
    (negociações) e no Envision (viagens realizadas, valores e ticket médio).

    Use quando a consultora pedir o perfil, o histórico de viagens, o ticket
    médio ou as negociações de um cliente.

    Args:
        cliente: nome ou e-mail do cliente, como a consultora escreveu.
        historico_completo: True só se a consultora pedir o histórico
            completo; o padrão são os últimos 12 meses.
        escolha: quando a consulta anterior devolveu vários clientes e a
            consultora escolheu um, o código [escolha: ...] do escolhido.

    Returns:
        Um resumo pronto, com fontes e período. Os números já vêm calculados:
        use-os como estão.
    """
    turno = TURNO_ATUAL.get()
    rd, envision, avisos = _criar_conectores(turno.user_id if turno else None)
    mapeamentos = carregar_mapeamentos()

    resultado = BuscadorPerfil(
        rd=rd,
        envision=envision,
        mapeamentos=mapeamentos,
    ).buscar(
        texto=cliente,
        historico_completo=bool(historico_completo),
        escolha=(escolha or "").strip() or None,
    )
    resultado.avisos = avisos + resultado.avisos

    _registrar(
        texto=cliente,
        resultado=resultado,
    )

    return {
        "status": resultado.situacao,
        "resumo": resumo_para_ia(resultado) + "\n" + _aviso_dados_fora_da_lista(mapeamentos),
    }
