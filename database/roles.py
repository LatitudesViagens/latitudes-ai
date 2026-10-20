from supabase import Client


def is_ti(client: Client) -> bool:
    """Confere no banco (função public.is_ti, migração 009) se quem está
    logado tem o papel TI. Qualquer falha conta como "não é TI"."""
    try:
        response = client.rpc("is_ti").execute()
    except Exception as error:
        print(
            f"[TI] Falha ao conferir o papel: {type(error).__name__}",
            flush=True,
        )
        return False

    return response.data is True


LEVEL_COORDINATOR = "coordenador"
LEVEL_OPERATOR = "operadora"


def operations_level(client: Client) -> str | None:
    """Nível de quem está logado na equipe de Operações (migração 015):
    "coordenador", "operadora" ou None. Qualquer falha conta como "fora"."""
    try:
        if client.rpc("is_operations_coordinator").execute().data is True:
            return LEVEL_COORDINATOR

        if client.rpc("is_operations").execute().data is True:
            return LEVEL_OPERATOR
    except Exception as error:
        print(
            f"[OPERACOES] Falha ao conferir o nível: {type(error).__name__}",
            flush=True,
        )

    return None


def can_read_suppliers(client: Client) -> bool:
    """Operações (os dois níveis) ou TI: a ÁGORA mostra a base de
    fornecedores. Conferido no banco (funções da migração 015)."""
    for function in ("is_operations", "is_ti"):
        try:
            if client.rpc(function).execute().data is True:
                return True
        except Exception as error:
            print(
                f"[OPERACOES] Falha ao conferir {function}: {type(error).__name__}",
                flush=True,
            )

    return False
