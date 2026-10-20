"""Equipe de Operações (migração 015). Só o TI lê a equipe toda e muda
(políticas RLS); cada pessoa enxerga apenas o próprio nível."""

from supabase import Client

from database.roles import LEVEL_COORDINATOR, LEVEL_OPERATOR

LEVELS = (LEVEL_COORDINATOR, LEVEL_OPERATOR)


def list_operations_team(client: Client) -> dict[str, str]:
    """{id da pessoa: nível}."""
    response = (
        client.table("operations_team")
        .select("user_id,level")
        .execute()
    )
    return {str(row["user_id"]): str(row["level"]) for row in response.data or []}


def set_operations_level(
    client: Client,
    user_id: str,
    level: str,
    changed_by: str,
) -> None:
    if level not in LEVELS:
        raise ValueError("Nível inválido.")

    client.table("operations_team").upsert(
        {
            "user_id": str(user_id),
            "level": level,
            "created_by": str(changed_by),
        },
        on_conflict="user_id",
    ).execute()


def remove_from_operations(
    client: Client,
    user_id: str,
) -> None:
    client.table("operations_team").delete().eq("user_id", str(user_id)).execute()
