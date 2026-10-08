from supabase import Client


ALLOWED_ROLES = {"user", "assistant", "system", "tool"}

# Status do turno, guardado na resposta do assistente (migração 008).
STATUS_PENDING = "pendente"
STATUS_PROCESSING = "processando"
STATUS_DONE = "concluida"
STATUS_ERROR = "erro"
STATUS_CANCELLED = "cancelada"
ALLOWED_STATUSES = {
    STATUS_PENDING,
    STATUS_PROCESSING,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_CANCELLED,
}
# Só a resposta reservada (ainda sem conteúdo) pode ser gravada vazia.
EMPTY_CONTENT_STATUSES = {
    STATUS_PENDING,
    STATUS_PROCESSING,
    STATUS_CANCELLED,
}


def add_message(
    client: Client,
    conversation_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
    attachments: list[dict] | None = None,
    status: str | None = None,
    reply_to: str | None = None,
) -> dict:
    if role not in ALLOWED_ROLES:
        raise ValueError(f"Tipo de mensagem inválido: {role}")

    if status is not None and status not in ALLOWED_STATUSES:
        raise ValueError(f"Status de mensagem inválido: {status}")

    clean_content = content.strip()
    may_be_empty = role == "assistant" and status in EMPTY_CONTENT_STATUSES

    if not clean_content and not may_be_empty:
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

    if status is not None:
        message_data["status"] = status

    if reply_to is not None:
        message_data["reply_to"] = str(reply_to)

    response = (
        client.table("messages")
        .insert(message_data)
        .execute()
    )

    if not response.data:
        raise RuntimeError("Não foi possível salvar a mensagem.")

    return response.data[0]


def update_reply(
    client: Client,
    message_id: str,
    *,
    status: str,
    content: str | None = None,
    sources: list[dict] | None = None,
    attachments: list[dict] | None = None,
    only_if_status: set[str] | None = None,
) -> dict | None:
    """Atualiza a resposta reservada do assistente (sempre o mesmo registro).

    O banco só permite alterar conteúdo, fontes, anexos e status de
    respostas do assistente nas conversas do próprio usuário (migração 008).

    Com only_if_status, só atualiza se o status atual estiver no conjunto e
    devolve None se não estiver (ex.: a pessoa clicou em Parar e a resposta
    terminou logo depois: o "interrompido" não é sobrescrito).
    """
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Status de mensagem inválido: {status}")

    update_data: dict = {"status": status}

    if content is not None:
        update_data["content"] = content.strip()

    if sources is not None:
        update_data["sources"] = sources

    if attachments is not None:
        update_data["attachments"] = attachments

    query = (
        client.table("messages")
        .update(update_data)
        .eq("id", str(message_id))
        .eq("role", "assistant")
    )

    if only_if_status is not None:
        query = query.in_("status", sorted(only_if_status))

    response = query.execute()

    if not response.data:
        if only_if_status is not None:
            return None

        raise RuntimeError("Não foi possível atualizar a resposta.")

    return response.data[0]


def get_message(
    client: Client,
    message_id: str,
) -> dict | None:
    response = (
        client.table("messages")
        .select("*")
        .eq("id", str(message_id))
        .limit(1)
        .execute()
    )

    return response.data[0] if response.data else None


def list_messages(
    client: Client,
    conversation_id: str,
    limit: int | None = None,
) -> list[dict]:
    """Mensagens em ordem de criação. Com `limit`, só as mais recentes (a tela
    não pode ficar mais lenta conforme a conversa cresce)."""
    query = (
        client.table("messages")
        .select("*")
        .eq("conversation_id", str(conversation_id))
    )

    if limit is None:
        return query.order("created_at").execute().data

    recentes = query.order("created_at", desc=True).limit(limit).execute().data
    return list(reversed(recentes))
