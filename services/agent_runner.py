from collections.abc import AsyncIterator
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "latitudes_agent" / ".env"

load_dotenv(dotenv_path=ENV_FILE)


from latitudes_agent.agent import root_agent


APP_NAME = "latitudes_ai"


async def _restore_history(
    session_service: InMemorySessionService,
    session,
    history: list[dict],
) -> None:
    for message in history:
        role = message.get("role")
        content = message.get("content", "").strip()

        if not content or role not in {"user", "assistant"}:
            continue

        if role == "user":
            author = "user"
            content_role = "user"
        else:
            author = root_agent.name
            content_role = "model"

        history_event = Event(
            author=author,
            content=types.Content(
                role=content_role,
                parts=[
                    types.Part(text=content),
                ],
            ),
        )

        await session_service.append_event(
            session,
            history_event,
        )


async def stream_agent(
    user_id: str,
    conversation_id: str,
    question: str,
    history: list[dict] | None = None,
) -> AsyncIterator[str]:
    clean_question = question.strip()

    if not clean_question:
        raise ValueError("A pergunta não pode estar vazia.")

    session_service = InMemorySessionService()

    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=str(user_id),
        session_id=str(conversation_id),
    )

    if history:
        await _restore_history(
            session_service=session_service,
            session=session,
            history=history,
        )

    user_message = types.Content(
        role="user",
        parts=[
            types.Part(text=clean_question),
        ],
    )

    run_config = RunConfig(
        streaming_mode=StreamingMode.SSE,
    )

    assembled_response = ""

    async for event in runner.run_async(
        user_id=str(user_id),
        session_id=str(conversation_id),
        new_message=user_message,
        run_config=run_config,
    ):
        if not event.content:
            continue

        event_text = "".join(
            part.text
            for part in event.content.parts
            if part.text
        )

        if not event_text:
            continue

        if event.partial:
            if event_text.startswith(assembled_response):
                text_chunk = event_text[len(assembled_response):]
            else:
                text_chunk = event_text

            if text_chunk:
                assembled_response += text_chunk
                yield text_chunk

        elif event.is_final_response():
            if not assembled_response:
                assembled_response = event_text
                yield event_text
            elif event_text.startswith(assembled_response):
                remaining_text = event_text[len(assembled_response):]

                if remaining_text:
                    assembled_response += remaining_text
                    yield remaining_text

    if not assembled_response.strip():
        raise RuntimeError(
            "O agente não retornou uma resposta final."
        )


async def ask_agent(
    user_id: str,
    conversation_id: str,
    question: str,
    history: list[dict] | None = None,
) -> str:
    response_chunks = []

    async for chunk in stream_agent(
        user_id=user_id,
        conversation_id=conversation_id,
        question=question,
        history=history,
    ):
        response_chunks.append(chunk)

    final_response = "".join(response_chunks).strip()

    if not final_response:
        raise RuntimeError(
            "O agente não retornou uma resposta final."
        )

    return final_response
