"""Executa turnos em segundo plano, fora da execução do script do Streamlit.

O Streamlit interrompe o script a cada clique (trocar de conversa, "Parar")
e não consegue interrompê-lo enquanto ele espera a IA. Rodando o turno numa
thread própria, a geração continua mesmo quando a pessoa navega, e a tela só
acompanha o status gravado no banco. Este módulo é importado (não reexecutado
a cada rerun), então o registro de turnos vale para todas as sessões do
processo.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import threading
import time
import traceback

from supabase import Client

from database.client import background_client, get_access_token, get_background_client
from services.chat_service import (
    TurnFailedError,
    interrupt_turn,
    run_turn_stream,
    update_title_from_first_question,
)
from services.timing_log import log_duration, log_event


@dataclass
class _TurnJob:
    thread: threading.Thread | None = None
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task | None = None
    cancel_requested: bool = False
    # Texto parcial da resposta enquanto a IA escreve (a tela lê daqui).
    partial_text: str = ""


_jobs: dict[str, _TurnJob] = {}
_lock = threading.Lock()
_warm_up_started = False


def warm_up() -> None:
    """Carrega o LiteLLM e o agente em segundo plano (~4 s), uma vez por
    processo, para a tela abrir rápido e a 1ª mensagem não esperar."""
    global _warm_up_started

    with _lock:
        if _warm_up_started:
            return

        _warm_up_started = True

    def load() -> None:
        started = time.perf_counter()

        try:
            import services.agent_runner  # noqa: F401
            import services.itinerary_metadata  # noqa: F401
            import services.personal_data_check  # noqa: F401
            import services.title_service  # noqa: F401
        except Exception:
            traceback.print_exc()
            return

        log_duration("[AQUECIMENTO] imports", started)

        # Uma resposta simulada carrega o resto (o ADK e o LiteLLM só
        # importam na 1ª resposta). Sem isso, a 1ª pergunta depois de iniciar
        # o servidor esperava ~45 s.
        try:
            elapsed = asyncio.run(services.agent_runner.warm_up_pipeline())
            log_event(f"[AQUECIMENTO] resposta_simulada {elapsed:.2f}s")
        except Exception as error:
            print(f"[AQUECIMENTO] Resposta simulada falhou: {type(error).__name__}", flush=True)

        # Cotação do dólar (PTAX, uma vez por dia): a 1ª resposta esperava
        # até 5 s pelo Banco Central para converter o custo.
        started = time.perf_counter()

        try:
            from services.custos import get_usd_brl_rate

            get_usd_brl_rate()
            log_duration("[AQUECIMENTO] cotacao_dolar", started)
        except Exception as error:
            print(f"[AQUECIMENTO] Cotação falhou: {type(error).__name__}", flush=True)

    threading.Thread(target=load, daemon=True, name="pre-carregamento").start()


def is_running(reply_id: str) -> bool:
    """Tarefa em andamento para a TELA. Depois do "Parar", conta como parada
    na hora, mesmo que a thread ainda leve alguns segundos para encerrar
    (ex.: no meio da busca na base de conhecimento, que não é interrompida no
    meio); antes a tela ficava em "Preparando a resposta" até a thread sair."""
    with _lock:
        job = _jobs.get(str(reply_id))

    return (
        job is not None
        and not job.cancel_requested
        and job.thread is not None
        and job.thread.is_alive()
    )


def partial_text(reply_id: str) -> str:
    """Texto que a IA já escreveu nesta resposta ("" se nada ainda)."""
    with _lock:
        job = _jobs.get(str(reply_id))

    return job.partial_text if job is not None and not job.cancel_requested else ""


def start_turn_job(
    client: Client,
    user_id: str,
    conversation_id: str,
    reply_id: str,
    question: str | None = None,
) -> bool:
    """Inicia o turno em segundo plano; False se ele já estiver rodando.

    Cada thread cria a própria conexão com o Supabase a partir do token
    (lido aqui, na thread da tela): a conexão da sessão usa HTTP/2 e travava
    quando a tela e as tarefas a usavam ao mesmo tempo.
    """
    reply_id = str(reply_id)
    access_token = get_access_token(client)

    with _lock:
        current = _jobs.get(reply_id)

    # "Tentar novamente" logo após "Parar": espera a tarefa antiga encerrar.
    if (
        current is not None
        and current.cancel_requested
        and current.thread is not None
    ):
        current.thread.join(timeout=3)

    with _lock:
        current = _jobs.get(reply_id)

        if current and current.thread and current.thread.is_alive():
            return False

        job = _TurnJob()
        job.thread = threading.Thread(
            target=_run_job,
            kwargs={
                "job": job,
                "access_token": access_token,
                "user_id": str(user_id),
                "conversation_id": str(conversation_id),
                "reply_id": reply_id,
                "question": question,
            },
            daemon=True,
            name=f"turno-{reply_id[:8]}",
        )
        _jobs[reply_id] = job
        job.thread.start()

    # O título (resumo do pedido) é gerado em paralelo à resposta, para já
    # estar pronto quando ela aparecer. Só age na primeira pergunta.
    threading.Thread(
        target=_update_title,
        kwargs={
            "access_token": access_token,
            "conversation_id": str(conversation_id),
        },
        daemon=True,
        name=f"titulo-{reply_id[:8]}",
    ).start()

    return True


def _update_title(
    access_token: str,
    conversation_id: str,
) -> None:
    try:
        update_title_from_first_question(
            client=get_background_client(access_token),
            conversation_id=conversation_id,
        )
    except Exception:
        traceback.print_exc()


def cancel_turn_job(
    client: Client,
    reply_id: str,
) -> None:
    """Botão "Parar": marca o turno como interrompido NA HORA e cancela a
    geração em segundo plano. Se a IA terminar logo depois, a resposta é
    descartada (as gravações da tarefa só valem para turnos em andamento)."""
    reply_id = str(reply_id)

    interrupt_turn(
        client=client,
        reply_id=reply_id,
    )

    with _lock:
        job = _jobs.get(reply_id)

    if job is None or job.thread is None or not job.thread.is_alive():
        return

    job.cancel_requested = True

    if job.loop is not None and job.task is not None:
        job.loop.call_soon_threadsafe(job.task.cancel)


_prefetch_pool = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="base-antecipada",
)


def _start_knowledge_prefetch(question: str | None):
    """Começa o cálculo da similaridade da pergunta já no início da tarefa
    (a consulta à base continua antes da IA; só deixa de esperar a conexão e a
    leitura da conversa). Devolve (pergunta, futuro) ou None."""
    text = str(question or "").strip()

    if not text:
        return None

    from services.base_conhecimento import base_known_empty, prefetch_question_embedding

    if base_known_empty():
        return None

    return text, _prefetch_pool.submit(prefetch_question_embedding, text)


def _run_job(
    job: _TurnJob,
    access_token: str,
    user_id: str,
    conversation_id: str,
    reply_id: str,
    question: str | None = None,
) -> None:
    loop = asyncio.new_event_loop()
    job.loop = loop

    started = time.perf_counter()
    knowledge_prefetch = _start_knowledge_prefetch(question)

    async def consume() -> None:
        def show_progress(text: str) -> None:
            job.partial_text = text

        # Conexão guardada da resposta anterior da mesma pessoa, se houver.
        with background_client(access_token) as client:
            log_duration(f"[TURN] reply={reply_id[:8]} conexao_propria", started)

            async for _ in run_turn_stream(
                client=client,
                user_id=user_id,
                conversation_id=conversation_id,
                reply_id=reply_id,
                knowledge_prefetch=knowledge_prefetch,
                progress_listener=show_progress,
            ):
                pass

    try:
        job.task = loop.create_task(consume())

        if job.cancel_requested:
            job.task.cancel()

        from services.amostrador_lento import SlowAttemptSampler

        # Se o turno passar de 20 s, mostra no log onde o tempo foi ([LENTO]).
        with SlowAttemptSampler(
            thread_id=threading.get_ident(),
            tag=f"reply={reply_id[:8]}",
        ):
            loop.run_until_complete(job.task)

        log_duration(f"[TURN] reply={reply_id[:8]} concluido", started)
    except asyncio.CancelledError:
        log_duration(f"[TURN] reply={reply_id[:8]} interrompido_pelo_usuario", started)
    except TurnFailedError as error:
        # Falha prevista: o turno já está como erro, com mensagem amigável.
        log_duration(
            f"[TURN] reply={reply_id[:8]} erro",
            started,
            details=type(error.__cause__).__name__,
        )
    except Exception:
        traceback.print_exc()
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass

        loop.close()

        with _lock:
            if _jobs.get(reply_id) is job:
                del _jobs[reply_id]
