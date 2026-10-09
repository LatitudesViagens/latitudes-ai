"""Medição temporária da lentidão da tela (só com ?medir=1 no endereço).

O servidor monta cada tela em ~0,3 s, mas a pessoa vê a mudança vários
segundos depois (08/10/2026). Este contador mostra, no canto da tela, onde o
tempo se perde em cada atualização:

- clique → servidor começar: o pedido chegando ao servidor;
- servidor: montagem da tela no servidor;
- servidor → navegador: a tela chegando ao navegador;
- desenho: o navegador desenhando a tela.

Nada é gravado nem enviado: só aparece para quem abriu com ?medir=1. Remover
quando a lentidão estiver resolvida.
"""

import json
import sys
import threading
import time
import traceback

import streamlit as st

STATE_KEY = "measure_screen_timing"
WATCHDOG_INTERVAL_SECONDS = 0.5
WATCHDOG_SAMPLE_SECONDS = 0.25
WATCHDOG_REPORT_SECONDS = 0.5

_watchdog_lock = threading.Lock()
_watchdog_started = False


def _frames_text(frame, limit: int = 12) -> str:
    """Pilha de chamadas resumida (só arquivos e funções do código)."""
    return "".join(traceback.format_stack(frame, limit=limit))


def _busy_threads(skip: set[int]) -> list[str]:
    """Threads que estão executando Python (não esperando) neste instante."""
    idle_markers = ("wait", "select", "sleep", "_recv", "accept", "poll", "get")
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    busy = []

    for ident, frame in sys._current_frames().items():
        if ident in skip or frame is None:
            continue

        if any(marker in frame.f_code.co_name for marker in idle_markers):
            continue

        busy.append(f"--- thread {names.get(ident, ident)}\n{_frames_text(frame, limit=8)}")

    return busy


def start_event_loop_watchdog() -> None:
    """Vigia a parte do Streamlit que recebe os cliques e envia as telas (o
    "event loop"). Se ela ficar presa mais de 0,5 s, escreve no terminal quanto
    tempo e o que estava rodando — é isso que atrasa clique e tela."""
    global _watchdog_started

    with _watchdog_lock:
        if _watchdog_started:
            return
        _watchdog_started = True

    from streamlit.runtime import Runtime

    loop = Runtime.instance()._get_async_objs().eventloop
    loop_thread: dict[str, int] = {}
    loop.call_soon_threadsafe(lambda: loop_thread.setdefault("id", threading.get_ident()))

    def watch() -> None:
        own_id = threading.get_ident()

        while True:
            time.sleep(WATCHDOG_INTERVAL_SECONDS)
            answered = threading.Event()
            started = time.perf_counter()
            loop.call_soon_threadsafe(answered.set)
            samples: list[str] = []

            while not answered.wait(WATCHDOG_SAMPLE_SECONDS):
                # Preso: guarda o que a parte travada e as demais estão fazendo.
                frame = sys._current_frames().get(loop_thread.get("id"))
                sample = "=== parte que recebe cliques e envia telas\n"
                sample += _frames_text(frame) if frame is not None else "(sem pilha)\n"
                sample += "\n".join(_busy_threads(skip={own_id, loop_thread.get("id", -1)}))
                samples.append(sample)

            lag = time.perf_counter() - started

            if lag >= WATCHDOG_REPORT_SECONDS:
                # A amostra do meio da trava costuma ser a mais representativa.
                culprit = samples[len(samples) // 2] if samples else "(trava curta, sem amostra)"
                print(
                    f"\n[TRAVA] {time.strftime('%H:%M:%S')} cliques e telas "
                    f"parados por {lag:.2f}s\n{culprit}",
                    flush=True,
                )

    threading.Thread(target=watch, daemon=True, name="vigia-da-tela").start()


def timing_enabled() -> bool:
    """Liga com ?medir=1 e continua ligado na sessão (o endereço muda ao
    trocar de conversa)."""
    if st.query_params.get("medir") == "1":
        st.session_state[STATE_KEY] = True

    return bool(st.session_state.get(STATE_KEY))


def show_timing_probe(run_started_epoch: float) -> None:
    """Desenha o contador; chamar no FIM da execução da tela."""
    if not timing_enabled():
        return

    try:
        start_event_loop_watchdog()
    except Exception:
        traceback.print_exc()

    server = json.dumps(
        {
            "inicio": round(run_started_epoch * 1000),
            "fim": round(time.time() * 1000),
        }
    )

    st.html(
        f"""
        <script>
        (() => {{
            const parentWindow = window.parent;
            const parentDocument = parentWindow.document;
            const server = {server};
            const chegou = Date.now();

            if (!parentWindow.__agoraMedir) {{
                parentWindow.__agoraMedir = {{ clique: null, linhas: [] }};
                const marcar = () => {{
                    parentWindow.__agoraMedir.clique = Date.now();
                }};
                parentDocument.addEventListener("pointerdown", marcar, true);
                parentDocument.addEventListener("keydown", (evento) => {{
                    if (evento.key === "Enter") {{
                        marcar();
                    }}
                }}, true);
            }}

            const estado = parentWindow.__agoraMedir;
            const segundos = (ms) => (ms / 1000).toFixed(2) + " s";

            requestAnimationFrame(() => requestAnimationFrame(() => {{
                const pintou = Date.now();
                const partes = [];
                const clique = estado.clique;

                // Só conta o clique se ele veio antes desta atualização.
                if (clique && clique <= server.inicio + 500) {{
                    partes.push("TOTAL " + segundos(pintou - clique));
                    partes.push("clique→servidor " + segundos(server.inicio - clique));
                }} else {{
                    partes.push("(sem clique)");
                }}

                partes.push("servidor " + segundos(server.fim - server.inicio));
                partes.push("servidor→navegador " + segundos(chegou - server.fim));
                partes.push("desenho " + segundos(pintou - chegou));
                estado.clique = null;

                const hora = new Date().toLocaleTimeString("pt-BR");
                estado.linhas.unshift(hora + "  " + partes.join(" | "));
                estado.linhas = estado.linhas.slice(0, 6);

                let caixa = parentDocument.getElementById("agora-medir");

                if (!caixa) {{
                    caixa = parentDocument.createElement("pre");
                    caixa.id = "agora-medir";
                    caixa.style.cssText = [
                        "position:fixed", "left:8px", "top:8px", "z-index:100000",
                        "margin:0", "padding:6px 8px", "max-width:96vw",
                        "white-space:pre-wrap", "font:11px/1.4 monospace",
                        "background:rgba(255,255,255,0.95)", "color:#333",
                        "border:1px solid #94825D", "border-radius:6px",
                        "pointer-events:none",
                    ].join(";");
                    parentDocument.body.appendChild(caixa);
                }}

                caixa.textContent = "MEDIÇÃO (só para você)\\n" + estado.linhas.join("\\n");
            }}));
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )
