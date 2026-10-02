from supabase import Client


def log_attempt(
    client: Client,
    message_id: str,
    conversation_id: str,
    attempt: dict,
) -> None:
    """Registra uma tentativa de resposta (migração 008).

    Os campos de tokens e custo ficam vazios quando o provedor não os
    informa; serão usados no controle de custos.
    """
    record = {
        "message_id": str(message_id),
        "conversation_id": str(conversation_id),
        "attempt_number": int(attempt["attempt_number"]),
        "model_role": str(attempt["model_role"]),
        "model": str(attempt["model"]),
        "status": str(attempt["status"]),
        "error_type": attempt.get("error_type"),
        "duration_ms": max(int(attempt.get("duration_ms") or 0), 0),
        "simulated": bool(attempt.get("simulated")),
        "started_at": str(attempt["started_at"]),
    }

    for key in (
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "cost_usd",
    ):
        if attempt.get(key) is not None:
            record[key] = attempt[key]

    client.table("message_attempts").insert(record).execute()
