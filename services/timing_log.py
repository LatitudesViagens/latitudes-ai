"""Registro de tempos para diagnosticar lentidão.

Sempre imprime no terminal. Com AGORA_LOG_TEMPOS=1, também grava em
logs/agora-tempos.log (fora do Git e da imagem). Só tempos, etapas e IDs:
nunca conteúdo de mensagens, e-mails ou tokens.
"""

from datetime import datetime
import os
from pathlib import Path
import threading
import time

LOG_FILE = Path(__file__).resolve().parents[1] / "logs" / "agora-tempos.log"

_lock = threading.Lock()


def log_event(message: str) -> None:
    line = f"{datetime.now():%H:%M:%S.%f}"[:-3] + f" {message}"
    print(line, flush=True)

    if os.getenv("AGORA_LOG_TEMPOS") != "1":
        return

    try:
        with _lock:
            LOG_FILE.parent.mkdir(exist_ok=True)

            with LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
    except OSError:
        pass


def log_duration(label: str, started: float, details: str = "") -> None:
    """Registra quanto tempo passou desde `started` (time.perf_counter())."""
    elapsed = time.perf_counter() - started
    log_event(f"{label} {elapsed:.2f}s" + (f" {details}" if details else ""))
