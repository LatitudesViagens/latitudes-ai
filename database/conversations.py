from supabase import Client


def create_conversation(
    client: Client,
    user_id: str,
    title: str = "Nova conversa",
) -> dict:
    response = (
        client.table("conversations")
        .insert(
            {
                "user_id": str(user_id),
                "title": title,
            }
        )
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Não foi possível criar a conversa."
        )

    return response.data[0]


def list_conversations(
    client: Client,
    user_id: str,
    limit: int | None = None,
) -> list[dict]:
    """Fixadas primeiro, depois as mais recentes. Com `limit`, só as primeiras
    (as demais aparecem pela busca ou por "Mostrar mais")."""
    query = (
        client.table("conversations")
        .select("*")
        .eq("user_id", str(user_id))
        .order("is_pinned", desc=True)
        .order("updated_at", desc=True)
    )

    if limit is not None:
        query = query.limit(limit)

    return query.execute().data


def get_conversation(
    client: Client,
    conversation_id: str,
) -> dict | None:
    response = (
        client.table("conversations")
        .select("*")
        .eq("id", str(conversation_id))
        .limit(1)
        .execute()
    )

    return response.data[0] if response.data else None


def delete_conversation(
    client: Client,
    conversation_id: str,
) -> None:
    (
        client.table("conversations")
        .delete()
        .eq("id", str(conversation_id))
        .execute()
    )


def set_conversation_pinned(
    client: Client,
    conversation_id: str,
    is_pinned: bool,
) -> None:
    response = (
        client.table("conversations")
        .update(
            {
                "is_pinned": is_pinned,
            }
        )
        .eq("id", str(conversation_id))
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Não foi possível atualizar a conversa."
        )

def rename_conversation(
    client: Client,
    conversation_id: str,
    title: str,
) -> dict:
    clean_title = " ".join(title.split()).strip()

    if not clean_title:
        raise ValueError(
            "O título da conversa não pode estar vazio."
        )

    if len(clean_title) > 80:
        raise ValueError(
            "O título da conversa deve ter no máximo 80 caracteres."
        )

    response = (
        client.table("conversations")
        .update(
            {
                "title": clean_title,
            }
        )
        .eq("id", str(conversation_id))
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Não foi possível renomear a conversa."
        )

    return response.data[0]

ALLOWED_VISIBILITIES = {"private", "public"}


def set_conversation_visibility(
    client: Client,
    conversation_id: str,
    visibility: str,
) -> dict:
    if visibility not in ALLOWED_VISIBILITIES:
        raise ValueError(
            "A visibilidade deve ser private ou public."
        )

    response = (
        client.table("conversations")
        .update(
            {
                "visibility": visibility,
            }
        )
        .eq("id", str(conversation_id))
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Não foi possível alterar a visibilidade da conversa."
        )

    return response.data[0]