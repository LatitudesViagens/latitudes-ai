"""Registro dos cadastros de usuários feitos pelo TI (migração 010). As
políticas RLS só deixam o TI gravar e ler."""

from supabase import Client


def log_user_registration(
    client: Client,
    user_id: str,
    full_name: str,
    email: str,
) -> dict:
    """Registra o cadastro (quem cadastrou = login do TI, preenchido pelo banco)."""
    response = (
        client.table("user_registrations")
        .insert(
            {
                "user_id": str(user_id),
                "full_name": full_name.strip(),
                "email": email.strip().lower(),
            }
        )
        .execute()
    )
    return response.data[0]


def list_user_registrations(
    client: Client,
    limit: int = 50,
) -> list[dict]:
    response = (
        client.table("user_registrations")
        .select("full_name,email,created_by,created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []
