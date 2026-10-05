"""Configurações de resiliência da ÁGORA lidas de variáveis de ambiente.

Valores inválidos voltam ao padrão (com aviso no log) em vez de derrubar o app.
"""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "latitudes_agent" / ".env"

load_dotenv(dotenv_path=ENV_FILE)


SIMULATED_FAILURE_KINDS = {"erro", "timeout", "vazia"}
SIMULATED_FAILURE_TARGETS = {"todas", "principal", "primeira"}

# Folga além do maior timeout antes de considerar um turno abandonado.
STALE_TURN_MARGIN_SECONDS = 30


@dataclass(frozen=True)
class SimulatedFailure:
    kind: str
    target: str


@dataclass(frozen=True)
class RetrySettings:
    primary_attempts: int
    fallback_attempts: int
    primary_timeout_seconds: float
    fallback_timeout_seconds: float
    initial_backoff_seconds: float
    simulated_failure: SimulatedFailure | None

    @property
    def stale_turn_seconds(self) -> float:
        """Tempo sem atualização após o qual um turno é dado como perdido.

        Cada tentativa atualiza o turno ao começar, então basta cobrir a
        tentativa mais longa e a espera mais longa entre tentativas.
        """
        total_attempts = self.primary_attempts + self.fallback_attempts
        longest_backoff = self.initial_backoff_seconds * 2 ** max(
            total_attempts - 2,
            0,
        )
        return (
            max(
                self.primary_timeout_seconds,
                self.fallback_timeout_seconds,
            )
            + longest_backoff
            + STALE_TURN_MARGIN_SECONDS
        )


def _number_from_env(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw_value = os.getenv(name, "").strip()

    if not raw_value:
        return default

    try:
        value = float(raw_value.replace(",", "."))
    except ValueError:
        value = None

    if value is None or not minimum <= value <= maximum:
        print(
            f"[CONFIG] {name}={raw_value!r} inválido; usando {default}.",
            flush=True,
        )
        return default

    return value


def _simulated_failure_from_env() -> SimulatedFailure | None:
    raw_value = os.getenv("AGORA_SIMULAR_FALHA", "").strip().lower()

    if not raw_value:
        return None

    kind, _, target = raw_value.partition(":")
    target = target or "todas"

    if (
        kind not in SIMULATED_FAILURE_KINDS
        or target not in SIMULATED_FAILURE_TARGETS
    ):
        print(
            f"[CONFIG] AGORA_SIMULAR_FALHA={raw_value!r} inválido; use "
            "erro, timeout ou vazia, com :todas, :principal ou :primeira. "
            "Simulação desligada.",
            flush=True,
        )
        return None

    return SimulatedFailure(kind=kind, target=target)


def load_retry_settings() -> RetrySettings:
    return RetrySettings(
        primary_attempts=int(
            _number_from_env("AGORA_TENTATIVAS_PRINCIPAL", 2, 1, 5)
        ),
        fallback_attempts=int(
            _number_from_env("AGORA_TENTATIVAS_FALLBACK", 1, 0, 5)
        ),
        primary_timeout_seconds=_number_from_env(
            "AGORA_TIMEOUT_PRINCIPAL_SEGUNDOS",
            60,
            5,
            600,
        ),
        fallback_timeout_seconds=_number_from_env(
            "AGORA_TIMEOUT_FALLBACK_SEGUNDOS",
            90,
            5,
            600,
        ),
        initial_backoff_seconds=_number_from_env(
            "AGORA_ESPERA_INICIAL_SEGUNDOS",
            2,
            0,
            60,
        ),
        simulated_failure=_simulated_failure_from_env(),
    )


_startup_settings = load_retry_settings()

if _startup_settings.simulated_failure is not None:
    print(
        "[CONFIG] ATENÇÃO: simulação de falhas LIGADA "
        f"({_startup_settings.simulated_failure.kind}:"
        f"{_startup_settings.simulated_failure.target}). "
        "Não use em produção.",
        flush=True,
    )
