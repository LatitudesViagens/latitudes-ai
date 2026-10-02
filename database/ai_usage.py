from supabase import Client


def log_ai_usage(
    client: Client,
    usages: list[dict],
    conversation_id: str | None = None,
) -> None:
    """Grava chamadas de IA fora das respostas (migração 009, tabela ai_usage).

    Cada item vem de services.custos.collect_usage. O usuário é o do login
    (user_id = auth.uid() no banco). Nunca interrompe quem chamou.
    """
    from services.custos import cost_fields

    records = []

    for usage in usages:
        record = {
            "purpose": usage["purpose"],
            "model": str(usage["model"]),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            **cost_fields(usage.get("cost_usd")),
        }

        if conversation_id:
            record["conversation_id"] = str(conversation_id)

        records.append(record)

    if not records:
        return

    try:
        client.table("ai_usage").insert(records).execute()
    except Exception as error:
        print(
            f"[CUSTOS] Falha ao registrar uso avulso: {type(error).__name__}",
            flush=True,
        )
