"""Mostra onde o tempo vai numa tentativa lenta da IA (diagnóstico).

Durante a tentativa, uma thread anota a cada 0,5 s a função que a tarefa está
executando. Se a tentativa passar de SLOW_SECONDS, imprime no log ([LENTO])
as funções que mais apareceram. Só nomes de arquivos e funções do código.
"""

from collections import Counter
import os
import sys
import threading
import time

SLOW_SECONDS = 20
SAMPLE_SECONDS = 0.5
_OWN_PREFIXES = ("services", "agent_tools", "latitudes_agent", "integracoes", "database")


def _frame_label(frame) -> str:
    """As 3 chamadas mais internas, destacando a mais próxima do nosso código."""
    labels = []
    ours = None

    while frame is not None:
        path = frame.f_code.co_filename.replace("\\", "/")
        short = "/".join(path.split("/")[-2:])
        label = f"{short}:{frame.f_code.co_name}"

        if len(labels) < 3:
            labels.append(label)

        if ours is None and any(f"/{prefix}/" in path for prefix in _OWN_PREFIXES):
            ours = label

        frame = frame.f_back

    return " ← ".join(labels) + (f"   [nosso código: {ours}]" if ours else "")


class SlowAttemptSampler:
    def __init__(self, thread_id: int, tag: str) -> None:
        self._thread_id = thread_id
        self._tag = tag
        self._samples: Counter[str] = Counter()
        self._stop = threading.Event()
        self._started = time.perf_counter()
        self._thread = threading.Thread(target=self._run, daemon=True, name="amostrador-lento")

    def __enter__(self):
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(SAMPLE_SECONDS):
            frame = sys._current_frames().get(self._thread_id)

            if frame is not None:
                self._samples[_frame_label(frame)] += 1

    def __exit__(self, *exc) -> None:
        self._stop.set()
        elapsed = time.perf_counter() - self._started

        if elapsed < SLOW_SECONDS or not self._samples:
            return

        print(f"\n[LENTO] {self._tag} tentativa de {elapsed:.1f}s; onde a tarefa estava (amostras a cada {SAMPLE_SECONDS}s):", flush=True)

        for label, count in self._samples.most_common(6):
            print(f"[LENTO]   {count * SAMPLE_SECONDS:5.1f}s  {label}", flush=True)
