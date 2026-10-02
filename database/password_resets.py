"""Registro de resets de senha feitos pelo TI e obrigação de trocar a senha
(migração 009). As políticas RLS só deixam o TI gravar e ler os resets."""

from supabase import Client


def log_password_reset(
    client: Client,
    target_user_id: str,
) -> dict:
    """Registra o reset (quem fez = login do TI, preenchido pelo banco)."""
    response = (
        client.table("password_resets")
        .insert({"target_user_id": str(target_user_id)})
        .execute()
    )
    return response.data[0]


def require_password_change(
    client: Client,
    user_id: str,
    reset_id: str | None,
) -> None:
    """Obriga a pessoa a criar uma senha nova no próximo acesso."""
    client.table("password_change_required").upsert(
        {
            "user_id": str(user_id),
            "reset_id": str(reset_id) if reset_id else None,
        },
        on_conflict="user_id",
    ).execute()


def must_change_password(
    client: Client,
    user_id: str,
) -> bool:
    response = (
        client.table("password_change_required")
        .select("user_id")
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    return bool(response.data)


def list_password_resets(
    client: Client,
    limit: int = 30,
) -> list[dict]:
    response = (
        client.table("password_resets")
        .select("target_user_id,reset_by,created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []
