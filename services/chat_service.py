import asyncio
from collections.abc import AsyncIterator
import re

from supabase import Client

from database.attachments import (
    hydrate_chat_attachments,
    upload_generated_files,
)
from database.conversations import rename_conversation
from database.messages import add_message, list_messages
from services.agent_runner import ask_agent, stream_agent
from services.title_service import generate_conversation_title


INTERRUPTED_RESPONSE = "Resposta interrompida pelo usuário."
FAILED_RESPONSE = (
    "Não foi possível concluir esta resposta porque o serviço de IA "
    "ficou temporariamente indisponível. Você pode tentar novamente."
)
MAX_HISTORY_MESSAGES = 30
ATTACHMENT_REFERENCE_PATTERN = re.compile(
    r"\b(anex|arquiv|imagem|foto|pdf|document|planilha|word|excel|csv)\w*\b",
    flags=re.IGNORECASE,
)


def _prepare_message(
    client: Client,
    conversation_id: str,
    content: str,
    attachments: list[dict] | None = None,
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
            attachments=attachments or [],
        )
        agent_history = history

    return user_message, agent_history, clean_content


def _prepare_history_for_agent(
    client: Client,
    history: list[dict],
    question: str,
    has_current_attachments: bool,
) -> list[dict]:
    # Mantém o contexto textual recente, mas não reenvia todos os binários de
    # toda a conversa em cada chamada. Isso reduz muito o payload multimodal.
    recent_history = history[-MAX_HISTORY_MESSAGES:]
    prepared_history = []

    previous_attachment_index = None

    if (
        not has_current_attachments
        and ATTACHMENT_REFERENCE_PATTERN.search(question)
    ):
        for index in range(len(recent_history) - 1, -1, -1):
            message = recent_history[index]

            if (
                message.get("role") == "user"
                and message.get("attachments")
            ):
                previous_attachment_index = index
                break

    for index, message in enumerate(recent_history):
        prepared_message = dict(message)
        prepared_message["attachments"] = []

        if index == previous_attachment_index:
            prepared_message["attachments"] = hydrate_chat_attachments(
                client=client,
                attachments=message.get("attachments") or [],
            )

        prepared_history.append(prepared_message)

    return prepared_history


GENERATED_FILE_FAILURE_NOTE = (
    "\n\n_Não foi possível salvar o arquivo gerado. "
    "Peça novamente em instantes._"
)


def _save_generated_files(
    client: Client,
    user_id: str,
    conversation_id: str,
    files: list[dict],
) -> list[dict] | None:
    """Salva os arquivos da resposta; None indica falha no salvamento."""
    if not files:
        return []

    try:
        return upload_generated_files(
            client=client,
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            files=files,
        )
    except Exception as error:
        print(
            f"[DOCUMENT] upload_error={type(error).__name__}",
            flush=True,
        )
        return None


def is_model_answer(message: dict) -> bool:
    if message.get("role") != "assistant":
        return False

    if message.get("content") in {FAILED_RESPONSE, INTERRUPTED_RESPONSE}:
        return False

    # A oferta de roteiro da memória coletiva não é uma resposta do modelo.
    return not any(
        isinstance(source, dict)
        and source.get("type") == "shared_itinerary_candidate"
        for source in message.get("sources") or []
    )


def update_title_after_first_answer(
    client: Client,
    conversation_id: str,
) -> str | None:
    """Troca o título provisório por um resumo gerado pela IA.

    Só age na primeira resposta válida do modelo, para não sobrescrever
    títulos renomeados pelo usuário depois. Mantém o título provisório se
    a geração falhar.
    """
    messages = list_messages(
        client=client,
        conversation_id=conversation_id,
    )

    answers = [
        message
        for message in messages
        if is_model_answer(message)
    ]

    if len(answers) != 1:
        return None

    first_question = next(
        (
            message.get("content", "")
            for message in messages
            if message.get("role") == "user"
        ),
        "",
    )

    title = generate_conversation_title(
        question=first_question,
        answer=answers[0].get("content", ""),
    )

    if not title:
        return None

    rename_conversation(
        client=client,
        conversation_id=conversation_id,
        title=title,
    )

    return title


def close_pending_response(
    client: Client,
    conversation_id: str,
    content: str = INTERRUPTED_RESPONSE,
) -> dict | None:
    """Fecha com segurança uma mensagem de usuário que ficou pendente."""
    history = list_messages(
        client=client,
        conversation_id=conversation_id,
    )

    if not history or history[-1].get("role") != "user":
        return None

    return add_message(
        client=client,
        conversation_id=conversation_id,
        role="assistant",
        content=content,
        sources=[],
    )


async def process_message(
    client: Client,
    user_id: str,
    conversation_id: str,
    content: str,
    attachments: list[dict] | None = None,
) -> dict:
    user_message, agent_history, clean_content = _prepare_message(
        client=client,
        conversation_id=conversation_id,
        content=content,
        attachments=attachments,
    )

    hydrated_history = _prepare_history_for_agent(
        client=client,
        history=agent_history,
        question=clean_content,
        has_current_attachments=bool(
            user_message.get("attachments")
        ),
    )
    current_attachments = hydrate_chat_attachments(
        client=client,
        attachments=user_message.get("attachments") or [],
    )

    sources = []
    generated_files = []

    assistant_content = await ask_agent(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        question=clean_content,
        history=hydrated_history,
        source_collector=sources,
        attachments=current_attachments,
        file_collector=generated_files,
    )

    saved_files = _save_generated_files(
        client=client,
        user_id=user_id,
        conversation_id=conversation_id,
        files=generated_files,
    )

    if saved_files is None:
        assistant_content += GENERATED_FILE_FAILURE_NOTE
        saved_files = []

    assistant_message = add_message(
        client=client,
        conversation_id=conversation_id,
        role="assistant",
        content=assistant_content,
        sources=sources,
        attachments=saved_files,
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
    attachments: list[dict] | None = None,
) -> AsyncIterator[str]:
    user_message, agent_history, clean_content = _prepare_message(
        client=client,
        conversation_id=conversation_id,
        content=content,
        attachments=attachments,
    )

    hydrated_history = _prepare_history_for_agent(
        client=client,
        history=agent_history,
        question=clean_content,
        has_current_attachments=bool(
            user_message.get("attachments")
        ),
    )
    current_attachments = hydrate_chat_attachments(
        client=client,
        attachments=user_message.get("attachments") or [],
    )

    response_chunks = []
    sources = []
    generated_files = []
    stopped = False

    try:
        async for chunk in stream_agent(
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            question=clean_content,
            history=hydrated_history,
            source_collector=sources,
            attachments=current_attachments,
            file_collector=generated_files,
        ):
            response_chunks.append(chunk)
            yield chunk

    except (GeneratorExit, asyncio.CancelledError):
        stopped = True
        raise
    finally:
        assistant_content = "".join(response_chunks).strip()

        if assistant_content and stopped:
            assistant_content += "\n\n_Resposta interrompida pelo usuário._"

        if assistant_content:
            saved_files = _save_generated_files(
                client=client,
                user_id=user_id,
                conversation_id=conversation_id,
                files=generated_files,
            )

            if saved_files is None:
                assistant_content += GENERATED_FILE_FAILURE_NOTE
                saved_files = []

            add_message(
                client=client,
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_content,
                sources=sources,
                attachments=saved_files,
            )
        elif stopped:
            close_pending_response(
                client=client,
                conversation_id=conversation_id,
                content=INTERRUPTED_RESPONSE,
            )

    if not response_chunks:
        raise RuntimeError(
            "O agente não retornou uma resposta final."
        )
