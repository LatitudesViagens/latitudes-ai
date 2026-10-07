"""Registro das exclusões de usuários feitas pelo TI (migração 011). As
políticas RLS só deixam o TI gravar e ler."""

from supabase import Client


def log_user_deletion(
    client: Client,
    deleted_user_id: str,
    email: str,
    full_name: str | None,
) -> None:
    """Registra a exclusão (quem excluiu = login do TI, preenchido pelo banco)."""
    client.table("user_deletions").insert(
        {
            "deleted_user_id": str(deleted_user_id),
            "email": email,
            "full_name": full_name,
        }
    ).execute()


def list_user_deletions(
    client: Client,
    limit: int = 50,
) -> list[dict]:
    response = (
        client.table("user_deletions")
        .select("full_name,email,deleted_by,created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []
