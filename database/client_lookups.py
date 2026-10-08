"""Registro das consultas de perfil de cliente (migração 012). Quem consultou
é preenchido pelo banco (auth.uid()); só o TI lê. Nunca grava CPF."""

from supabase import Client


def log_client_lookup(
    client: Client,
    conversation_id: str | None,
    search_text: str,
    outcome: str,
    systems: list[str],
    client_name: str | None = None,
    client_email: str | None = None,
) -> None:
    record = {
        "search_text": search_text.strip()[:200] or "(vazio)",
        "outcome": outcome,
        "systems": systems,
        "client_name": client_name,
        "client_email": client_email,
    }

    if conversation_id:
        record["conversation_id"] = str(conversation_id)

    client.table("client_lookups").insert(record).execute()
