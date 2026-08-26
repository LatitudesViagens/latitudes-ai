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
        raise RuntimeError("Não foi possível criar a conversa.")

    return response.data[0]


def list_conversations(
    client: Client,
    user_id: str,
) -> list[dict]:
    response = (
        client.table("conversations")
        .select("*")
        .eq("user_id", str(user_id))
        .order("updated_at", desc=True)
        .execute()
    )

    return response.data