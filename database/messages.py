from supabase import Client


ALLOWED_ROLES = {"user", "assistant", "system", "tool"}


def add_message(
    client: Client,
    conversation_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
    attachments: list[dict] | None = None,
) -> dict:
    if role not in ALLOWED_ROLES:
        raise ValueError(f"Tipo de mensagem inválido: {role}")

    clean_content = content.strip()

    if not clean_content:
        raise ValueError("A mensagem não pode estar vazia.")

    message_data = {
        "conversation_id": str(conversation_id),
        "role": role,
        "content": clean_content,
    }

    if sources is not None:
        message_data["sources"] = sources

    if attachments is not None:
        message_data["attachments"] = attachments

    response = (
        client.table("messages")
        .insert(message_data)
        .execute()
    )

    if not response.data:
        raise RuntimeError("Não foi possível salvar a mensagem.")

    return response.data[0]


def list_messages(
    client: Client,
    conversation_id: str,
) -> list[dict]:
    response = (
        client.table("messages")
        .select("*")
        .eq("conversation_id", str(conversation_id))
        .order("created_at")
        .execute()
    )

    return response.data
