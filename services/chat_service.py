import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
import re

from supabase import Client

from database.attachments import (
    hydrate_chat_attachments,
    upload_generated_files,
)
from database.ai_usage import log_ai_usage
from database.conversations import rename_conversation
from database.message_attempts import log_attempt
from database.messages import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_PROCESSING,
    add_message,
    get_message,
    list_messages,
    update_reply,
)
from services.custos import collect_usage, cost_fields
from services.settings import RetrySettings, load_retry_settings
from services.timing_log import log_event

# services.agent_runner e services.title_service carregam o LiteLLM e o
# agente (~4 s). São importados só quando a IA é usada (e pré-carregados em
# segundo plano por services.turn_worker.warm_up), para a tela abrir rápido.


# Respostas gravadas por versões anteriores do app (antes da migração 008).
INTERRUPTED_RESPONSE = "Resposta interrompida pelo usuário."
FAILED_RESPONSE = (
    "Não foi possível concluir esta resposta porque o serviço de IA "
    "ficou temporariamente indisponível. Você pode tentar novamente."
)

# Mensagens mostradas ao usuário quando o turno termina em erro. Detalhes
# técnicos ficam só no registro de tentativas e no log.
TURN_FAILED_MESSAGE = (
    "Não consegui concluir esta resposta agora. Sua mensagem está salva: "
    "use **Tentar novamente** em instantes."
)
SERVICE_UNAVAILABLE_MESSAGE = (
    "O serviço de IA está indisponível no momento. Sua mensagem está salva. "
    "Avise a equipe responsável pela ÁGORA."
)
INTERRUPTED_MESSAGE = (
    "A resposta foi interrompida antes de terminar. Use **Tentar "
    "novamente** para continuar ou **Cancelar** para editar sua mensagem."
)

ACTIVE_STATUSES = {STATUS_PENDING, STATUS_PROCESSING}
# Estados em que o turno pode ser executado (de novo).
RUNNABLE_STATUSES = {STATUS_PENDING, STATUS_PROCESSING, STATUS_ERROR}


class TurnFailedError(RuntimeError):
    """O turno terminou em erro; a mensagem amigável já está gravada."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class TurnInProgressError(RuntimeError):
    """Já existe uma resposta em andamento nesta conversa."""
MAX_HISTORY_MESSAGES = 30
ATTACHMENT_REFERENCE_PATTERN = re.compile(
    r"\b(anex|arquiv|imagem|foto|pdf|document|planilha|word|excel|csv)\w*\b",
    flags=re.IGNORECASE,
)


def message_status(message: dict) -> str:
    # Mensagens anteriores à migração 008 não têm status: estão concluídas.
    return str(message.get("status") or STATUS_DONE)


def _parse_timestamp(value) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _cancelled_user_ids(messages: list[dict]) -> set[str]:
    return {
        str(message["reply_to"])
        for message in messages
        if message.get("role") == "assistant"
        and message_status(message) == STATUS_CANCELLED
        and message.get("reply_to")
    }


def visible_messages(messages: list[dict]) -> list[dict]:
    """Esconde turnos cancelados (pergunta e resposta) da conversa."""
    cancelled_user_ids = _cancelled_user_ids(messages)

    return [
        message
        for message in messages
        if str(message.get("id")) not in cancelled_user_ids
        and not (
            message.get("role") == "assistant"
            and message_status(message) == STATUS_CANCELLED
        )
    ]


def is_turn_active(
    reply: dict,
    settings: RetrySettings | None = None,
    now: datetime | None = None,
) -> bool:
    """Resposta em andamento e ainda dentro do tempo esperado."""
    if message_status(reply) not in ACTIVE_STATUSES:
        return False

    settings = settings or load_retry_settings()
    updated_at = _parse_timestamp(
        reply.get("updated_at") or reply.get("created_at")
    )

    if updated_at is None:
        return False

    now = now or datetime.now(timezone.utc)
    return (now - updated_at).total_seconds() <= settings.stale_turn_seconds


def recover_stale_turns(
    client: Client,
    messages: list[dict],
    settings: RetrySettings | None = None,
) -> list[dict]:
    """Marca como erro turnos presos em pendente/processando.

    Acontece quando a página é recarregada ou a conexão cai no meio da
    resposta. Cada tentativa atualiza o turno ao começar, então um turno só é
    considerado perdido quando para de ser atualizado por mais tempo que o
    timeout mais longo.
    """
    settings = settings or load_retry_settings()
    recovered = []

    for message in messages:
        if (
            message.get("role") == "assistant"
            and message_status(message) in ACTIVE_STATUSES
            and not is_turn_active(message, settings=settings)
        ):
            try:
                message = update_reply(
                    client=client,
                    message_id=message["id"],
                    status=STATUS_ERROR,
                    content=INTERRUPTED_MESSAGE,
                )
            except Exception as error:
                print(
                    f"[TURN] stale_recovery_error={type(error).__name__}",
                    flush=True,
                )

        recovered.append(message)

    return recovered


def find_open_turn(messages: list[dict]) -> dict | None:
    """Último turno sem resposta concluída: {"user_message", "reply"}.

    "reply" é None quando a mensagem do usuário ficou sem resposta reservada
    (conversas antigas ou falha ao reservar).
    """
    visible = visible_messages(messages)
    last_user = next(
        (
            message
            for message in reversed(visible)
            if message.get("role") == "user"
        ),
        None,
    )

    if last_user is None:
        return None

    reply = next(
        (
            message
            for message in visible
            if message.get("role") == "assistant"
            and str(message.get("reply_to")) == str(last_user["id"])
        ),
        None,
    )

    if reply is None:
        # Conversas antigas: a resposta vinha logo depois, sem reply_to.
        last_index = visible.index(last_user)
        answered = any(
            message.get("role") == "assistant"
            for message in visible[last_index + 1:]
        )
        return None if answered else {"user_message": last_user, "reply": None}

    if message_status(reply) == STATUS_DONE:
        return None

    return {"user_message": last_user, "reply": reply}


def start_turn(
    client: Client,
    conversation_id: str,
    content: str,
    attachments: list[dict] | None = None,
    new_conversation: bool = False,
) -> dict:
    """Grava a mensagem do usuário e reserva a resposta ANTES da IA.

    Retorna {"user_message", "reply"}. Se a reserva da resposta falhar, a
    mensagem do usuário continua salva e o turno pode ser retomado depois.
    Numa conversa recém-criada não há turno anterior a verificar.
    """
    clean_content = content.strip()

    if not clean_content:
        raise ValueError("A mensagem não pode estar vazia.")

    if not new_conversation:
        history = list_messages(
            client=client,
            conversation_id=conversation_id,
        )
        open_turn = find_open_turn(history)

        if (
            open_turn is not None
            and open_turn["reply"] is not None
            and is_turn_active(open_turn["reply"])
        ):
            raise TurnInProgressError(
                "Aguarde a resposta anterior terminar."
            )

    user_message = add_message(
        client=client,
        conversation_id=conversation_id,
        role="user",
        content=clean_content,
        attachments=attachments or [],
    )
    reply = reserve_reply(
        client=client,
        conversation_id=conversation_id,
        user_message_id=user_message["id"],
    )

    return {
        "user_message": user_message,
        "reply": reply,
    }


def reserve_reply(
    client: Client,
    conversation_id: str,
    user_message_id: str,
) -> dict:
    """Cria o registro de resposta que todas as tentativas vão atualizar."""
    return add_message(
        client=client,
        conversation_id=conversation_id,
        role="assistant",
        content="",
        status=STATUS_PENDING,
        reply_to=user_message_id,
    )


def complete_reply(
    client: Client,
    reply_id: str,
    content: str,
    sources: list[dict] | None = None,
    attachments: list[dict] | None = None,
    only_if_active: bool = False,
) -> dict | None:
    """Grava a resposta final. Com only_if_active, não sobrescreve um turno
    que já foi interrompido (retorna None nesse caso)."""
    return update_reply(
        client=client,
        message_id=reply_id,
        status=STATUS_DONE,
        content=content,
        sources=sources if sources is not None else [],
        attachments=attachments if attachments is not None else [],
        only_if_status=ACTIVE_STATUSES if only_if_active else None,
    )


def cancel_turn(
    client: Client,
    reply_id: str,
) -> dict:
    """Cancela o turno: pergunta e resposta somem da conversa e do histórico
    da IA, mas continuam no banco para auditoria e controle de custos."""
    return update_reply(
        client=client,
        message_id=reply_id,
        status=STATUS_CANCELLED,
        content="",
    )


def interrupt_turn(
    client: Client,
    reply_id: str,
) -> None:
    """Marca como interrompido um turno ainda em andamento (botão "Parar"
    quando não há tarefa viva para cancelar)."""
    reply = get_message(
        client=client,
        message_id=reply_id,
    )

    if reply is not None and message_status(reply) in ACTIVE_STATUSES:
        _mark_reply_error(client, reply_id, INTERRUPTED_MESSAGE)


def _mark_reply_error(
    client: Client,
    reply_id: str,
    content: str,
) -> None:
    try:
        # Só marca turnos ainda em andamento: um "interrompido" gravado pelo
        # botão Parar não é trocado por outra mensagem.
        update_reply(
            client=client,
            message_id=reply_id,
            status=STATUS_ERROR,
            content=content,
            only_if_status=ACTIVE_STATUSES,
        )
    except Exception as error:
        # Se nem isso for possível, o turno vira erro sozinho ao ficar parado.
        print(
            f"[TURN] mark_error_failed={type(error).__name__}",
            flush=True,
        )


def _history_for_agent(
    messages: list[dict],
    user_message: dict,
) -> list[dict]:
    """Mensagens anteriores ao turno, só com turnos concluídos."""
    user_index = next(
        index
        for index, message in enumerate(messages)
        if str(message.get("id")) == str(user_message["id"])
    )
    previous = messages[:user_index]
    unfinished_user_ids = {
        str(message["reply_to"])
        for message in previous
        if message.get("role") == "assistant"
        and message.get("reply_to")
        and message_status(message) != STATUS_DONE
    }

    return [
        message
        for message in previous
        if str(message.get("id")) not in unfinished_user_ids
        and not (
            message.get("role") == "assistant"
            and (
                message_status(message) != STATUS_DONE
                or message.get("content") in {
                    FAILED_RESPONSE,
                    INTERRUPTED_RESPONSE,
                }
            )
        )
    ]


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

    if message_status(message) != STATUS_DONE:
        return False

    if message.get("content") in {FAILED_RESPONSE, INTERRUPTED_RESPONSE}:
        return False

    # A oferta de roteiro da memória coletiva não é uma resposta do modelo.
    return not any(
        isinstance(source, dict)
        and source.get("type") == "shared_itinerary_candidate"
        for source in message.get("sources") or []
    )


def update_title_from_first_question(
    client: Client,
    conversation_id: str,
) -> str | None:
    """Troca o título provisório por um resumo do que a pessoa pediu.

    Só age enquanto a conversa tem uma única pergunta e nenhuma resposta
    concluída, para não sobrescrever títulos renomeados depois. Roda em
    paralelo à resposta (services/turn_worker.py), então o título já está
    pronto quando a resposta aparece. Mantém o título provisório se falhar.
    """
    messages = visible_messages(
        list_messages(
            client=client,
            conversation_id=conversation_id,
        )
    )

    questions = [
        message
        for message in messages
        if message.get("role") == "user"
    ]
    answered = any(
        is_model_answer(message)
        for message in messages
    )

    if len(questions) != 1 or answered:
        return None

    from services.title_service import generate_conversation_title

    with collect_usage() as usages:
        title = generate_conversation_title(
            question=str(questions[0].get("content", "")),
            answer="",
        )

    log_ai_usage(
        client=client,
        usages=usages,
        conversation_id=conversation_id,
    )

    if not title:
        return None

    rename_conversation(
        client=client,
        conversation_id=conversation_id,
        title=title,
    )

    return title


def _attempt_listener(
    client: Client,
    reply_id: str,
    conversation_id: str,
    knowledge_entry_id: str | None = None,
):
    """Registra cada tentativa e mantém o turno "vivo" enquanto roda."""

    def listener(event: dict) -> None:
        if event.get("event") == "start":
            # Atualiza updated_at: o turno não é dado como perdido enquanto
            # houver tentativas começando. Não reabre turno já interrompido.
            update_reply(
                client=client,
                message_id=reply_id,
                status=STATUS_PROCESSING,
                only_if_status=ACTIVE_STATUSES,
            )
            return

        log_event(
            f"[IA] reply={str(reply_id)[:8]} tentativa={event.get('attempt_number')} "
            f"{event.get('model_role')} {event.get('model')} "
            f"status={event.get('status')} {(event.get('duration_ms') or 0) / 1000:.2f}s "
            f"erro={event.get('error_type')} custo_usd={event.get('cost_usd')}"
        )
        log_attempt(
            client=client,
            message_id=reply_id,
            conversation_id=conversation_id,
            # Custo em R$ pela cotação do dia (PTAX ou reserva).
            attempt={
                **event,
                **cost_fields(event.get("cost_usd")),
                "knowledge_entry_id": knowledge_entry_id,
            },
        )

    return listener


def _find_knowledge_match(
    client: Client,
    conversation_id: str,
    question: str,
    user_message: dict,
    first_question: bool,
):
    """Procura uma resposta aprovada parecida (services/base_conhecimento).
    Perguntas com anexo seguem direto para a IA. Nunca lança erro."""
    if user_message.get("attachments"):
        return None

    try:
        from services.base_conhecimento import find_knowledge_match

        with collect_usage() as usages:
            knowledge_match = find_knowledge_match(
                client=client,
                question=question,
                first_question=first_question,
            )

        log_ai_usage(
            client=client,
            usages=usages,
            conversation_id=conversation_id,
        )
        return knowledge_match
    except Exception as error:
        print(f"[BASE] Falha ao consultar a base: {type(error).__name__}", flush=True)
        return None


def _answer_from_knowledge_base(
    client: Client,
    conversation_id: str,
    reply_id: str,
    knowledge_match,
) -> str | None:
    """Responde com a resposta aprovada, sem chamar a IA (custo zero)."""
    from services.base_conhecimento import DIRECT_ANSWER_NOTE

    started_at = datetime.now(timezone.utc)
    content = knowledge_match.answer.strip() + DIRECT_ANSWER_NOTE
    completed = complete_reply(
        client=client,
        reply_id=reply_id,
        content=content,
        sources=[
            {
                "type": "knowledge_entry",
                "id": knowledge_match.entry_id,
                "mode": "direta",
                "similarity": round(knowledge_match.similarity, 4),
            }
        ],
        only_if_active=True,
    )

    try:
        log_attempt(
            client=client,
            message_id=reply_id,
            conversation_id=conversation_id,
            attempt={
                "attempt_number": 1,
                "model_role": "base_conhecimento",
                "model": "base_conhecimento",
                "status": "sucesso",
                "duration_ms": int(
                    (datetime.now(timezone.utc) - started_at).total_seconds()
                    * 1000
                ),
                "started_at": started_at.isoformat(),
                "simulated": False,
                "cost_usd": 0,
                "cost_brl": 0,
                "knowledge_entry_id": knowledge_match.entry_id,
            },
        )
    except Exception as error:
        print(f"[BASE] Falha ao registrar a resposta da base: {type(error).__name__}", flush=True)

    log_event(
        f"[BASE] reply={str(reply_id)[:8]} respondida pela base "
        f"(similaridade {knowledge_match.similarity:.3f})"
    )

    return content if completed is not None else None


def _save_client_lookups(
    client: Client,
    turno,
) -> None:
    """Grava em client_lookups (migração 012) as consultas de perfil de
    cliente anotadas pelas ferramentas neste turno. Nunca lança erro."""
    if not turno.consultas_cliente:
        return

    from database.client_lookups import log_client_lookup

    for consulta in turno.consultas_cliente:
        try:
            log_client_lookup(
                client=client,
                conversation_id=turno.conversation_id,
                **consulta,
            )
        except Exception as error:
            print(
                f"[PERFIL] Falha ao registrar a consulta: {type(error).__name__}",
                flush=True,
            )

    turno.consultas_cliente.clear()


async def run_turn_stream(
    client: Client,
    user_id: str,
    conversation_id: str,
    reply_id: str,
) -> AsyncIterator[str]:
    """Executa (ou repete) um turno, sempre atualizando a MESMA resposta.

    Sucesso: grava o conteúdo e marca 'concluida'. Falha de todas as
    tentativas: marca 'erro' com mensagem amigável e lança TurnFailedError.
    Interrupção (página recarregada, "Parar"): marca 'erro' e repassa.
    """
    messages = list_messages(
        client=client,
        conversation_id=conversation_id,
    )
    reply = next(
        (
            message
            for message in messages
            if str(message.get("id")) == str(reply_id)
        ),
        None,
    )

    if reply is None or message_status(reply) not in RUNNABLE_STATUSES:
        raise RuntimeError("Este turno não pode ser executado.")

    user_message = next(
        message
        for message in messages
        if str(message.get("id")) == str(reply.get("reply_to"))
    )
    question = str(user_message.get("content", "")).strip()

    update_reply(
        client=client,
        message_id=reply_id,
        status=STATUS_PROCESSING,
        content="",
    )

    agent_history = _history_for_agent(messages, user_message)
    knowledge_match = _find_knowledge_match(
        client=client,
        conversation_id=conversation_id,
        question=question,
        user_message=user_message,
        first_question=not any(
            message.get("role") == "assistant"
            for message in agent_history
        ),
    )

    if knowledge_match is not None and knowledge_match.mode == "direta":
        content = _answer_from_knowledge_base(
            client=client,
            conversation_id=conversation_id,
            reply_id=reply_id,
            knowledge_match=knowledge_match,
        )

        if content:
            yield content

        return

    from services import agent_runner
    from services.contexto_turno import TURNO_ATUAL, TurnoAtual

    # Ferramentas que consultam sistemas da Latitudes anotam as consultas
    # aqui (services/contexto_turno.py); o registro é gravado no fim do turno.
    turno_atual = TurnoAtual(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
    )
    TURNO_ATUAL.set(turno_atual)

    response_chunks = []
    sources = []
    generated_files = []

    try:
        hydrated_history = _prepare_history_for_agent(
            client=client,
            history=agent_history,
            question=question,
            has_current_attachments=bool(user_message.get("attachments")),
        )

        if knowledge_match is not None:
            # Resposta aprovada parecida: a IA responde usando-a como base.
            from services.base_conhecimento import build_context_message

            hydrated_history.append(build_context_message(knowledge_match))
        current_attachments = hydrate_chat_attachments(
            client=client,
            attachments=user_message.get("attachments") or [],
        )

        async for chunk in agent_runner.stream_agent(
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            question=question,
            history=hydrated_history,
            source_collector=sources,
            attachments=current_attachments,
            file_collector=generated_files,
            attempt_listener=_attempt_listener(
                client=client,
                reply_id=reply_id,
                conversation_id=conversation_id,
                knowledge_entry_id=(
                    knowledge_match.entry_id if knowledge_match else None
                ),
            ),
        ):
            response_chunks.append(chunk)
            yield chunk

    except (GeneratorExit, asyncio.CancelledError):
        _mark_reply_error(client, reply_id, INTERRUPTED_MESSAGE)
        raise
    except agent_runner.AgentUnavailableError as error:
        _mark_reply_error(client, reply_id, SERVICE_UNAVAILABLE_MESSAGE)
        raise TurnFailedError(SERVICE_UNAVAILABLE_MESSAGE) from error
    except Exception as error:
        print(
            f"[TURN] failed error={type(error).__name__}",
            flush=True,
        )
        _mark_reply_error(client, reply_id, TURN_FAILED_MESSAGE)
        raise TurnFailedError(TURN_FAILED_MESSAGE) from error
    finally:
        # Toda consulta de cliente é registrada, mesmo se o turno falhar.
        _save_client_lookups(
            client=client,
            turno=turno_atual,
        )

    assistant_content = "".join(response_chunks).strip()

    if knowledge_match is not None:
        # A tela mostra que a resposta se baseou na base aprovada pelo TI.
        sources.append(
            {
                "type": "knowledge_entry",
                "id": knowledge_match.entry_id,
                "mode": "contexto",
                "similarity": round(knowledge_match.similarity, 4),
            }
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

    completed = complete_reply(
        client=client,
        reply_id=reply_id,
        content=assistant_content,
        sources=sources,
        attachments=saved_files,
        only_if_active=True,
    )

    if completed is None:
        # A pessoa clicou em Parar enquanto a resposta terminava: vale o
        # "interrompido" já gravado.
        print(
            f"[TURN] reply={reply_id} resposta descartada (interrompida)",
            flush=True,
        )


async def process_message(
    client: Client,
    user_id: str,
    conversation_id: str,
    content: str,
    attachments: list[dict] | None = None,
) -> dict:
    """Versão sem streaming (cliente de terminal): mesmo fluxo de turno."""
    turn = start_turn(
        client=client,
        conversation_id=conversation_id,
        content=content,
        attachments=attachments,
    )

    async for _ in run_turn_stream(
        client=client,
        user_id=user_id,
        conversation_id=conversation_id,
        reply_id=turn["reply"]["id"],
    ):
        pass

    return {
        "user_message": turn["user_message"],
        "assistant_message": get_message(
            client=client,
            message_id=turn["reply"]["id"],
        ),
    }
