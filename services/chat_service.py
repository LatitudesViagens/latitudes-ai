from supabase import Client

from database.messages import add_message
from services.agent_runner import ask_agent


async def process_message(
    client: Client,
    user_id: str,
    conversation_id: str,
    content: str,
) -> dict:
    clean_content = content.strip()

    if not clean_content:
        raise ValueError("A mensagem não pode estar vazia.")

    user_message = add_message(
        client=client,
        conversation_id=conversation_id,
        role="user",
        content=clean_content,
    )

    assistant_content = await ask_agent(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        question=clean_content,
    )

    assistant_message = add_message(
        client=client,
        conversation_id=conversation_id,
        role="assistant",
        content=assistant_content,
    )

    return {
        "user_message": user_message,
        "assistant_message": assistant_message,
    }