from collections.abc import AsyncIterator

from supabase import Client

from database.messages import add_message, list_messages
from services.agent_runner import ask_agent, stream_agent


def _prepare_message(
    client: Client,
    conversation_id: str,
    content: str,
) -> tuple[dict, list[dict], str]:
    clean_content = content.strip()

    if not clean_content:
        raise ValueError("A mensagem não pode estar vazia.")

    history = list_messages(
        client=client,
        conversation_id=conversation_id,
    )

    last_message = history[-1] if history else None

    if last_message and last_message["role"] == "user":
        if last_message["content"] != clean_content:
            raise RuntimeError(
                "Existe uma mensagem anterior aguardando resposta."
            )

        user_message = last_message
        agent_history = history[:-1]
    else:
        user_message = add_message(
            client=client,
            conversation_id=conversation_id,
            role="user",
            content=clean_content,
        )
        agent_history = history

    return user_message, agent_history, clean_content


async def process_message(
    client: Client,
    user_id: str,
    conversation_id: str,
    content: str,
) -> dict:
    user_message, agent_history, clean_content = _prepare_message(
        client=client,
        conversation_id=conversation_id,
        content=content,
    )

    sources = []

    assistant_content = await ask_agent(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        question=clean_content,
        history=agent_history,
        source_collector=sources,
    )

    assistant_message = add_message(
        client=client,
        conversation_id=conversation_id,
        role="assistant",
        content=assistant_content,
        sources=sources,
    )

    return {
        "user_message": user_message,
        "assistant_message": assistant_message,
    }


async def process_message_stream(
    client: Client,
    user_id: str,
    conversation_id: str,
    content: str,
) -> AsyncIterator[str]:
    _, agent_history, clean_content = _prepare_message(
        client=client,
        conversation_id=conversation_id,
        content=content,
    )

    response_chunks = []
    sources = []

    async for chunk in stream_agent(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        question=clean_content,
        history=agent_history,
        source_collector=sources,
    ):
        response_chunks.append(chunk)
        yield chunk

    assistant_content = "".join(response_chunks).strip()

    if not assistant_content:
        raise RuntimeError(
            "O agente não retornou uma resposta final."
        )

    add_message(
        client=client,
        conversation_id=conversation_id,
        role="assistant",
        content=assistant_content,
        sources=sources,
    )