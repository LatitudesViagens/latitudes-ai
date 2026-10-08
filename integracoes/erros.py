"""Erros dos conectores. As mensagens são fixas e em português: nunca levam
URL, parâmetros, cabeçalhos ou corpo da resposta (o token do RD vai na URL e a
resposta pode ter dados de clientes)."""

MENSAGENS = {
    "credencial_ausente": "Credencial do {sistema} não configurada (variável de ambiente vazia ou ausente).",
    "credencial": "O {sistema} recusou a credencial (401).",
    "permissao": "A credencial não tem permissão para esta consulta no {sistema}.",
    "nao_encontrado": "Registro não encontrado no {sistema}.",
    "limite": "Limite de consultas do {sistema} atingido. Tente em instantes.",
    "timeout": "O {sistema} não respondeu a tempo.",
    "conexao": "Não foi possível conectar ao {sistema}.",
    "resposta": "O {sistema} devolveu uma resposta inesperada.",
    "servidor": "Erro no {sistema}. Tente novamente mais tarde.",
}


class IntegracaoError(Exception):
    """Falha numa consulta a um sistema externo."""

    def __init__(
        self,
        sistema: str,
        tipo: str,
        status_http: int | None = None,
    ) -> None:
        self.sistema = sistema
        self.tipo = tipo
        self.status_http = status_http
        super().__init__(MENSAGENS[tipo].format(sistema=sistema))


class ChamadaBloqueadaError(Exception):
    """Tentativa de chamada fora da lista de leitura permitida. Nunca sai da
    máquina: é levantada antes de qualquer requisição."""

    def __init__(
        self,
        sistema: str,
        metodo: str,
        caminho: str,
    ) -> None:
        self.sistema = sistema
        self.metodo = metodo
        self.caminho = caminho
        super().__init__(
            f"Chamada bloqueada no {sistema}: {metodo} {caminho} não está na "
            "lista de consultas permitidas (acesso somente leitura)."
        )
