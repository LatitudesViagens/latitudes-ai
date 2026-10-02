"""Base de conhecimento (migração 009). As políticas RLS garantem: qualquer
pessoa só SUGERE (entra como pendente); só o TI aprova, edita, rejeita e
apaga; todos leem as aprovadas."""

from datetime import datetime, timezone

from supabase import Client

STATUS_PENDING = "pendente"
STATUS_APPROVED = "aprovada"
STATUS_REJECTED = "rejeitada"

ENTRY_COLUMNS = (
    "id,question,answer,status,suggested_by,reviewed_by,reviewed_at,"
    "review_note,source_message_id,created_at,updated_at"
)


def suggest_entry(
    client: Client,
    question: str,
    answer: str,
    source_message_id: str | None,
) -> dict:
    record = {
        "question": question.strip(),
        "answer": answer.strip(),
        "status": STATUS_PENDING,
    }

    if source_message_id:
        record["source_message_id"] = str(source_message_id)

    response = client.table("knowledge_entries").insert(record).execute()
    return response.data[0]


def find_suggestion_for_message(
    client: Client,
    source_message_id: str,
) -> dict | None:
    response = (
        client.table("knowledge_entries")
        .select("id,status")
        .eq("source_message_id", str(source_message_id))
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def list_entries(
    client: Client,
    status: str,
    limit: int = 100,
) -> list[dict]:
    response = (
        client.table("knowledge_entries")
        .select(ENTRY_COLUMNS)
        .eq("status", status)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []


def create_approved_entry(
    client: Client,
    question: str,
    answer: str,
    embedding: str,
    embedding_model: str,
    reviewer_id: str,
) -> dict:
    """Entrada criada direto pelo TI (já aprovada)."""
    response = (
        client.table("knowledge_entries")
        .insert(
            {
                "question": question.strip(),
                "answer": answer.strip(),
                "status": STATUS_APPROVED,
                "embedding": embedding,
                "embedding_model": embedding_model,
                "reviewed_by": str(reviewer_id),
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .execute()
    )
    return response.data[0]


def approve_entry(
    client: Client,
    entry_id: str,
    question: str,
    answer: str,
    embedding: str,
    embedding_model: str,
    reviewer_id: str,
) -> None:
    """Aprova (ou salva a edição de) uma entrada, com o embedding novo."""
    response = (
        client.table("knowledge_entries")
        .update(
            {
                "question": question.strip(),
                "answer": answer.strip(),
                "status": STATUS_APPROVED,
                "embedding": embedding,
                "embedding_model": embedding_model,
                "reviewed_by": str(reviewer_id),
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("id", str(entry_id))
        .execute()
    )

    if not response.data:
        raise RuntimeError("Entrada não encontrada ou sem permissão.")


def reject_entry(
    client: Client,
    entry_id: str,
    reviewer_id: str,
    note: str | None = None,
) -> None:
    """Rejeita uma sugestão ou retira uma entrada aprovada da base."""
    response = (
        client.table("knowledge_entries")
        .update(
            {
                "status": STATUS_REJECTED,
                "embedding": None,
                "reviewed_by": str(reviewer_id),
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "review_note": (note or "").strip() or None,
            }
        )
        .eq("id", str(entry_id))
        .execute()
    )

    if not response.data:
        raise RuntimeError("Entrada não encontrada ou sem permissão.")


def has_approved_entries(client: Client) -> bool:
    response = (
        client.table("knowledge_entries")
        .select("id")
        .eq("status", STATUS_APPROVED)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def match_entries(
    client: Client,
    embedding: str,
    match_count: int = 1,
) -> list[dict]:
    """Entradas aprovadas mais parecidas (similaridade de 0 a 1)."""
    response = client.rpc(
        "match_knowledge_entries",
        {
            "query_embedding": embedding,
            "match_count": match_count,
        },
    ).execute()
    return response.data or []
