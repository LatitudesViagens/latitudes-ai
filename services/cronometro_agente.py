"""Tempo de cada ida ao modelo e de cada ferramenta do agente (log [AGENTE]).

Uma resposta pode ter várias idas ao modelo (ex.: decide pesquisar, recebe o
resultado, escreve) e várias ferramentas. Sem isto, o log só mostrava o total
da IA (ex.: 48 s) sem dizer onde o tempo foi. Só nomes e tempos: nada do
conteúdo da pergunta, da pesquisa ou do cliente.
"""

import threading
import time

from services.contexto_turno import TURNO_ATUAL

_lock = threading.Lock()
_started: dict[tuple, float] = {}


def _tag() -> str:
    turno = TURNO_ATUAL.get()
    return f"conversa={str(turno.conversation_id)[:8]} " if turno is not None else ""


def _start(key: tuple) -> None:
    with _lock:
        _started[key] = time.perf_counter()


def _elapsed(key: tuple) -> float | None:
    with _lock:
        started = _started.pop(key, None)

    return None if started is None else time.perf_counter() - started


def before_model(callback_context, llm_request):
    _start(("modelo", callback_context.invocation_id))
    return None


def after_model(callback_context, llm_response):
    elapsed = _elapsed(("modelo", callback_context.invocation_id))

    if elapsed is not None:
        parts = getattr(getattr(llm_response, "content", None), "parts", None) or []
        tools = [part.function_call.name for part in parts if getattr(part, "function_call", None)]
        action = f"pediu {', '.join(tools)}" if tools else "escreveu a resposta"
        print(f"[AGENTE] {_tag()}modelo {elapsed:.2f}s ({action})", flush=True)

    return None


def before_tool(tool, args, tool_context):
    _start(("ferramenta", tool_context.function_call_id))
    return None


def after_tool(tool, args, tool_context, tool_response):
    elapsed = _elapsed(("ferramenta", tool_context.function_call_id))

    if elapsed is not None:
        print(f"[AGENTE] {_tag()}ferramenta {tool.name} {elapsed:.2f}s", flush=True)

    return None
