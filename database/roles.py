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
