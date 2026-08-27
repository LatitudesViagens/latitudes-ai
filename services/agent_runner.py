from pathlib import Path

from dotenv import load_dotenv
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "latitudes_agent" / ".env"

load_dotenv(dotenv_path=ENV_FILE)


from latitudes_agent.agent import root_agent


APP_NAME = "latitudes_ai"

_session_service = InMemorySessionService()

_runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=_session_service,
)


async def _restore_history(
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

        await _session_service.append_event(
            session,
            history_event,
        )


async def ask_agent(
    user_id: str,
    conversation_id: str,
    question: str,
    history: list[dict] | None = None,
) -> str:
    clean_question = question.strip()

    if not clean_question:
        raise ValueError("A pergunta não pode estar vazia.")

    session = await _session_service.get_session(
        app_name=APP_NAME,
        user_id=str(user_id),
        session_id=str(conversation_id),
    )

    if session is None:
        session = await _session_service.create_session(
            app_name=APP_NAME,
            user_id=str(user_id),
            session_id=str(conversation_id),
        )

        if history:
            await _restore_history(
                session=session,
                history=history,
            )

    user_message = types.Content(
        role="user",
        parts=[
            types.Part(text=clean_question),
        ],
    )

    final_response = None

    async for event in _runner.run_async(
        user_id=str(user_id),
        session_id=str(conversation_id),
        new_message=user_message,
    ):
        if event.is_final_response() and event.content:
            text_parts = [
                part.text
                for part in event.content.parts
                if part.text
            ]

            if text_parts:
                final_response = "".join(text_parts)

    if not final_response:
        raise RuntimeError("O agente não retornou uma resposta final.")

    return final_response.strip()