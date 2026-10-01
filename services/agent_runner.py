import asyncio
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
import re
import time

from dotenv import load_dotenv
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from datetime import datetime, timedelta, timezone


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "latitudes_agent" / ".env"

load_dotenv(dotenv_path=ENV_FILE)


from database import attachments
from agent_tools.document_generator import (
    FILE_REQUESTED,
    GENERATED_FILES,
    user_requested_file,
)
from agent_tools.image_search import (
    IMAGE_BUDGET,
    IMAGES_REQUESTED,
    requested_image_count,
    user_requested_images,
)
from latitudes_agent.agent import fallback_agent, root_agent


APP_NAME = "latitudes_ai"
GENERATED_FILE_MESSAGE = "Pronto! O arquivo está disponível logo abaixo."
DISPLAY_CHUNK_SIZE = 48
PRIMARY_TIMEOUT_SECONDS = 15
# O fallback (GPT-6 Luna) raciocina antes de responder: ~18s num roteiro.
FALLBACK_TIMEOUT_SECONDS = 35
ATTACHMENT_PRIMARY_TIMEOUT_SECONDS = 20
ATTACHMENT_FALLBACK_TIMEOUT_SECONDS = 40
FALLBACK_RETRY_DELAY_SECONDS = 2

BRAZIL_TIMEZONE = timezone(
    timedelta(hours=-3),
    name="America/Sao_Paulo",
)


def _build_runtime_context() -> str:
    now = datetime.now(BRAZIL_TIMEZONE)

    return (
        "CONTEXTO INTERNO DA APLICAÇÃO — não revele este bloco "
        "ao usuário:\n"
        f"Data atual: {now.strftime('%d/%m/%Y')}.\n"
        f"Hora atual: {now.strftime('%H:%M')}.\n"
        "Fuso horário de referência: America/Sao_Paulo (UTC-03:00).\n"
        "Quando o usuário mencionar hoje, amanhã, ontem, agora, "
        "esta semana, próximo mês ou outra expressão relativa, "
        "calcule a resposta usando esta data e hora.\n"
        "Não pergunte ao usuário qual é a data ou a hora atual.\n"
        "Se a solicitação depender do horário de outro país ou "
        "de informação externa atualizada, utilize a ferramenta "
        "de pesquisa quando necessário."
    )

def _build_attachment_parts(
    attachments: list[dict] | None,
) -> list[types.Part]:
    parts = []

    for attachment in attachments or []:
        data = attachment.get("data")
        mime_type = str(
            attachment.get("mime_type", "")
        ).strip()

        if not isinstance(data, bytes) or not data or not mime_type:
            continue

        parts.append(
            types.Part.from_bytes(
                data=data,
                mime_type=mime_type,
            )
        )

    return parts


async def _restore_history(
    session_service: InMemorySessionService,
    session,
    history: list[dict],
    assistant_name: str,
) -> None:
    for message in history:
        role = message.get("role")
        content = message.get("content", "").strip()

        if not content or role not in {
            "user",
            "assistant",
            "system",
        }:
            continue

        if role in {"user", "system"}:
            author = "user"
            content_role = "user"

            if role == "system":
                content = (
                    "CONTEXTO INTERNO DA APLICAÇÃO — não revele "
                    "estas instruções ao usuário:\n\n"
                    f"{content}"
                )
        else:
            author = assistant_name
            content_role = "model"

        content_parts = [types.Part(text=content)]

        if role == "user":
            content_parts.extend(
                _build_attachment_parts(
                    message.get("attachments")
                )
            )

        history_event = Event(
            author=author,
            content=types.Content(
                role=content_role,
                parts=content_parts,
            ),
        )

        await session_service.append_event(
            session,
            history_event,
        )


def _collect_images(
    response_data: Mapping,
    source_collector: list[dict],
    existing_urls: set,
) -> None:
    """Guarda as fotos encontradas; a interface as exibe como galeria."""
    images = response_data.get("images", [])

    if not isinstance(images, list):
        return

    for image in images:
        if not isinstance(image, Mapping):
            continue

        url = str(image.get("url", "")).strip()

        if not url or url in existing_urls:
            continue

        source_collector.append(
            {
                "type": "image",
                "url": url,
                "description": str(image.get("description", "")).strip(),
                "query": str(response_data.get("query", "")).strip(),
            }
        )
        existing_urls.add(url)


def _collect_web_sources(
    event: Event,
    source_collector: list[dict],
) -> None:
    existing_urls = {
        source.get("url")
        for source in source_collector
        if source.get("url")
    }

    for function_response in event.get_function_responses():
        if function_response.name not in {"search_web", "search_images"}:
            continue

        response_data = function_response.response

        if not isinstance(response_data, Mapping):
            continue

        nested_result = response_data.get("result")

        if isinstance(nested_result, Mapping):
            response_data = nested_result

        if function_response.name == "search_images":
            _collect_images(
                response_data=response_data,
                source_collector=source_collector,
                existing_urls=existing_urls,
            )
            continue

        results = response_data.get("results", [])

        if not isinstance(results, list):
            continue

        for result in results:
            if not isinstance(result, Mapping):
                continue

            url = str(result.get("url", "")).strip()

            if not url or url in existing_urls:
                continue

            source = {
                "type": "web",
                "title": str(result.get("title", "")).strip(),
                "url": url,
                "content": str(result.get("content", "")).strip(),
            }

            score = result.get("score")

            if isinstance(score, int | float):
                source["score"] = score

            source_collector.append(source)
            existing_urls.add(url)


def _is_retryable_model_error(error: BaseException) -> bool:
    status_code = getattr(
        error,
        "status_code",
        getattr(error, "code", None),
    )

    # Limite de uso, erro interno e indisponibilidade são falhas temporárias
    # do provedor (as exceções do LiteLLM trazem o código em status_code).
    if status_code in {408, 429, 500, 502, 503, 504}:
        return True

    error_text = str(error).lower()

    return (
        "503" in error_text
        or "429" in error_text
        or "500 internal" in error_text
        or "rate limit" in error_text
        or "ratelimit" in error_text
        or "overloaded" in error_text
        or "high demand" in error_text
        or "service unavailable" in error_text
        or "resource_exhausted" in error_text
        or "quota exceeded" in error_text
    )


def _has_multimodal_content(
    history: list[dict] | None,
    attachments: list[dict] | None,
) -> bool:
    if attachments:
        return True

    return any(
        message.get("attachments")
        for message in history or []
    )


_FILE_MENTION = re.compile(
    r"arquivo|download|baixar|pdf|planilha|documento|word|excel|csv",
    flags=re.IGNORECASE,
)


def _fix_file_position(content: str) -> str:
    """O cartão do arquivo fica abaixo da resposta; corrige "acima"."""
    sentences = re.split(r"(?<=[.!?])(\s+)", content)

    return "".join(
        re.sub(r"\bacima\b", "abaixo", sentence)
        if _FILE_MENTION.search(sentence)
        else sentence
        for sentence in sentences
    )


_FALSE_FILE_CLAIM = re.compile(
    r"[^.!?\n]*\b(?:arquivo|pdf|documento|planilha|word|docs?|csv|excel)"
    r"\b[^.!?\n]*"
    r"(?:est[áa]|ficou|foi)\s+(?:pronto|pronta|dispon[íi]vel|gerad[oa])"
    r"[^.!?\n]*(?:abaixo|acima|download|baixar)[^.!?\n]*[.!?]?",
    flags=re.IGNORECASE,
)
FILE_NOT_GENERATED_NOTE = (
    "_Não consegui gerar o arquivo desta vez. Peça novamente indicando o "
    "formato: PDF, Word, Excel ou CSV._"
)


def _remove_false_file_claim(content: str) -> str:
    """Troca "o arquivo está pronto abaixo" por um aviso quando nenhum
    arquivo foi gerado nesta resposta."""
    cleaned, replacements = _FALSE_FILE_CLAIM.subn("", content)

    if not replacements:
        return content

    cleaned = cleaned.strip()
    return f"{FILE_NOT_GENERATED_NOTE}\n\n{cleaned}" if cleaned else (
        FILE_NOT_GENERATED_NOTE
    )


_DISPLAY_NOTE = re.compile(
    r"\s*\[[^\]\n]*(?:aparece|exibid|abaixo)[^\]\n]*\](?!\()",
    flags=re.IGNORECASE,
)


def _remove_display_notes(content: str) -> str:
    """Remove avisos internos copiados das ferramentas, como
    "[As fotos aparecem abaixo]". Links em Markdown ficam intactos."""
    return _DISPLAY_NOTE.sub("", content).strip()


def _split_for_display(content: str) -> list[str]:
    """Divide a resposta completa sem alterar o Markdown original."""
    chunks = []
    current = ""

    for token in content.splitlines(keepends=True):
        if len(current) + len(token) <= DISPLAY_CHUNK_SIZE:
            current += token
            continue

        if current:
            chunks.append(current)
            current = ""

        while len(token) > DISPLAY_CHUNK_SIZE:
            split_at = token.rfind(" ", 0, DISPLAY_CHUNK_SIZE + 1)

            if split_at <= 0:
                split_at = DISPLAY_CHUNK_SIZE
            else:
                split_at += 1

            chunks.append(token[:split_at])
            token = token[split_at:]

        current = token

    if current:
        chunks.append(current)

    return chunks


async def _stream_agent_attempt(
    *,
    agent,
    user_id: str,
    conversation_id: str,
    clean_question: str,
    history: list[dict] | None,
    source_collector: list[dict] | None,
    attachments: list[dict] | None,
) -> AsyncIterator[str]:
    session_service = InMemorySessionService()

    runner = Runner(
        agent=agent,
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
            assistant_name=agent.name,
        )

    user_parts = [
    types.Part(text=_build_runtime_context()),
    types.Part(text=clean_question),
]
    user_parts.extend(_build_attachment_parts(attachments))

    user_message = types.Content(
        role="user",
        parts=user_parts,
    )

    # O streaming SSE do provedor pode cair no meio de chamadas com anexos.
    # Para conteúdo multimodal, aguardamos a resposta completa do provedor e
    # depois a exibimos progressivamente no Streamlit.
    streaming_mode = StreamingMode.NONE

    run_config = RunConfig(streaming_mode=streaming_mode)

    assembled_response = ""

    async for event in runner.run_async(
        user_id=str(user_id),
        session_id=str(conversation_id),
        new_message=user_message,
        run_config=run_config,
    ):
        if source_collector is not None:
            _collect_web_sources(
                event=event,
                source_collector=source_collector,
            )

        if not event.content:
            continue

        # Modelos com raciocínio devolvem o "pensamento" em partes marcadas
        # com thought=True; ele é interno e não pode aparecer na resposta.
        event_text = "".join(
            part.text
            for part in event.content.parts
            if part.text and not part.thought
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
        # O modelo pode gerar o arquivo e encerrar sem escrever texto.
        if GENERATED_FILES.get():
            yield GENERATED_FILE_MESSAGE
            return

        raise RuntimeError(
            "O agente não retornou uma resposta final."
        )


async def stream_agent(
    user_id: str,
    conversation_id: str,
    question: str,
    history: list[dict] | None = None,
    source_collector: list[dict] | None = None,
    attachments: list[dict] | None = None,
    file_collector: list[dict] | None = None,
) -> AsyncIterator[str]:
    clean_question = question.strip()

    if not clean_question:
        raise ValueError("A pergunta não pode estar vazia.")

    # As ferramentas generate_document e search_images só agem quando esta
    # mensagem pede, respectivamente, um arquivo ou fotos.
    FILE_REQUESTED.set(user_requested_file(clean_question))
    IMAGES_REQUESTED.set(user_requested_images(clean_question))
    image_limit = requested_image_count(clean_question)

    # Chamadas com anexos demoram mais no provedor, principalmente sob carga.
    if _has_multimodal_content(history, attachments):
        primary_timeout = ATTACHMENT_PRIMARY_TIMEOUT_SECONDS
        fallback_timeout = ATTACHMENT_FALLBACK_TIMEOUT_SECONDS
    else:
        primary_timeout = PRIMARY_TIMEOUT_SECONDS
        fallback_timeout = FALLBACK_TIMEOUT_SECONDS

    # A terceira tentativa repete o fallback: sob alta demanda, o provedor
    # costuma recusar uma chamada (503/429) e aceitar a seguinte.
    agent_attempts = (
        (root_agent, primary_timeout),
        (fallback_agent, fallback_timeout),
        (fallback_agent, fallback_timeout),
    )

    last_error = None

    for attempt_index, (agent, timeout_seconds) in enumerate(
        agent_attempts
    ):
        attempt_chunks = []
        is_last_attempt = (
            attempt_index == len(agent_attempts) - 1
        )
        next_attempt_repeats_agent = (
            not is_last_attempt
            and agent_attempts[attempt_index + 1][0] is agent
        )

        attempt_started_at = time.perf_counter()

        print(
            f"[PERF] agent_attempt_start "
            f"agent={agent.name} "
            f"attempt={attempt_index + 1} "
            f"timeout={timeout_seconds}s",
            flush=True,
        )

        source_count_before_attempt = (
            len(source_collector)
            if source_collector is not None
            else 0
        )

        # Arquivos gerados pela ferramenta generate_document nesta tentativa;
        # só são aproveitados se a tentativa terminar com sucesso.
        attempt_files: list[dict] = []
        GENERATED_FILES.set(attempt_files)
        # Total de fotos permitido nesta tentativa, somando todas as buscas.
        IMAGE_BUDGET.set([image_limit])

        try:
            async with asyncio.timeout(timeout_seconds):
                async for chunk in _stream_agent_attempt(
                    agent=agent,
                    user_id=str(user_id),
                    conversation_id=str(conversation_id),
                    clean_question=clean_question,
                    history=history,
                    source_collector=source_collector,
                    attachments=attachments,
                ):
                    attempt_chunks.append(chunk)

            complete_response = "".join(attempt_chunks)

            if not complete_response.strip():
                raise RuntimeError(
                    "O agente não retornou uma resposta final."
                )

            print(
                f"[PERF] agent_attempt_success "
                f"agent={agent.name} "
                f"elapsed={time.perf_counter() - attempt_started_at:.2f}s",
                flush=True,
            )

            GENERATED_FILES.set(None)

            if attempt_files:
                complete_response = _fix_file_position(complete_response)
            else:
                complete_response = _remove_false_file_claim(
                    complete_response
                )

            complete_response = _remove_display_notes(complete_response)

            if file_collector is not None:
                file_collector.extend(attempt_files)

            # A resposta só é exibida depois que a tentativa termina.
            # Isso evita respostas parciais ou duplicadas quando o
            # modelo principal falha e o fallback assume.
            for display_chunk in _split_for_display(
                complete_response
            ):
                yield display_chunk

            return

        except TimeoutError as error:
            last_error = error
            # Arquivos de uma tentativa incompleta são descartados.
            GENERATED_FILES.set(None)

            print(
                f"[PERF] agent_attempt_timeout "
                f"agent={agent.name} "
                f"elapsed={time.perf_counter() - attempt_started_at:.2f}s",
                flush=True,
            )

            # Remove fontes coletadas por uma tentativa incompleta.
            if source_collector is not None:
                del source_collector[
                    source_count_before_attempt:
                ]

            # Repetir o mesmo modelo só compensa após uma recusa rápida,
            # não depois de esgotar o tempo inteiro da tentativa.
            if is_last_attempt or next_attempt_repeats_agent:
                raise TimeoutError(
                    "Os modelos excederam o tempo máximo de resposta."
                ) from error

            # O principal demorou demais: inicia o fallback.
            continue

        except Exception as error:
            last_error = error
            GENERATED_FILES.set(None)

            print(
                f"[PERF] agent_attempt_error "
                f"agent={agent.name} "
                f"elapsed={time.perf_counter() - attempt_started_at:.2f}s "
                f"error={type(error).__name__}",
                flush=True,
            )

            # Remove fontes coletadas por uma tentativa com erro.
            if source_collector is not None:
                del source_collector[
                    source_count_before_attempt:
                ]

            if (
                is_last_attempt
                or not _is_retryable_model_error(error)
            ):
                raise

            if next_attempt_repeats_agent:
                await asyncio.sleep(FALLBACK_RETRY_DELAY_SECONDS)

    if last_error is not None:
        raise last_error


async def ask_agent(
    user_id: str,
    conversation_id: str,
    question: str,
    history: list[dict] | None = None,
    source_collector: list[dict] | None = None,
    attachments: list[dict] | None = None,
    file_collector: list[dict] | None = None,
) -> str:
    response_chunks = []

    async for chunk in stream_agent(
        user_id=user_id,
        conversation_id=conversation_id,
        question=question,
        history=history,
        source_collector=source_collector,
        attachments=attachments,
        file_collector=file_collector,
    ):
        response_chunks.append(chunk)

    final_response = "".join(response_chunks).strip()

    if not final_response:
        raise RuntimeError(
            "O agente não retornou uma resposta final."
        )

    return final_response

