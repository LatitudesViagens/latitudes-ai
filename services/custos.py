"""Custo das chamadas de IA em US$ (valor informado pelo OpenRouter) e R$.

A conversão usa a cotação PTAX de venda do Banco Central (API pública, sem
chave), consultada no máximo uma vez por dia por servidor. Se o Banco
Central não responder, usa AGORA_COTACAO_DOLAR_RESERVA.

Módulo leve (sem LiteLLM): é importado também pela tela do painel.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "latitudes_agent" / ".env"

load_dotenv(dotenv_path=ENV_FILE)

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

PTAX_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoDolarPeriodo(dataInicial=@dataInicial,"
    "dataFinalCotacao=@dataFinalCotacao)"
)
PTAX_TIMEOUT_SECONDS = 5
# Fim de semana e feriado não têm cotação: usa a última dos últimos dias.
PTAX_LOOKBACK_DAYS = 7
# Depois de uma falha, espera antes de tentar o Banco Central de novo.
PTAX_RETRY_AFTER_SECONDS = 600

RESERVE_RATE_ENV = "AGORA_COTACAO_DOLAR_RESERVA"


@dataclass(frozen=True)
class ExchangeRate:
    rate: float
    rate_date: date | None
    source: str  # "ptax" ou "reserva"


_rate_lock = threading.Lock()
_cached_rate: tuple[date, ExchangeRate] | None = None
_last_failure_at: float | None = None


def _today() -> date:
    return datetime.now(SAO_PAULO).date()


def _fetch_ptax(today: date) -> ExchangeRate:
    start = today - timedelta(days=PTAX_LOOKBACK_DAYS)
    response = httpx.get(
        PTAX_URL,
        params={
            "@dataInicial": f"'{start:%m-%d-%Y}'",
            "@dataFinalCotacao": f"'{today:%m-%d-%Y}'",
            "$format": "json",
            "$select": "cotacaoVenda,dataHoraCotacao",
        },
        timeout=PTAX_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    quotes = response.json().get("value") or []

    if not quotes:
        raise RuntimeError("PTAX sem cotações no período.")

    latest = quotes[-1]
    rate = float(latest["cotacaoVenda"])

    if rate <= 0:
        raise RuntimeError("Cotação PTAX inválida.")

    quote_date = datetime.fromisoformat(
        str(latest["dataHoraCotacao"])[:10]
    ).date()

    return ExchangeRate(
        rate=rate,
        rate_date=quote_date,
        source="ptax",
    )


def _reserve_rate() -> ExchangeRate | None:
    raw_value = os.getenv(RESERVE_RATE_ENV, "").strip().replace(",", ".")

    try:
        rate = float(raw_value)
    except ValueError:
        return None

    if rate <= 0:
        return None

    return ExchangeRate(
        rate=rate,
        rate_date=None,
        source="reserva",
    )


def get_usd_brl_rate() -> ExchangeRate | None:
    """Cotação do dólar para converter custos; None se não houver nenhuma."""
    global _cached_rate, _last_failure_at

    today = _today()

    with _rate_lock:
        if _cached_rate is not None and _cached_rate[0] == today:
            return _cached_rate[1]

        recently_failed = (
            _last_failure_at is not None
            and time.monotonic() - _last_failure_at < PTAX_RETRY_AFTER_SECONDS
        )

        if not recently_failed:
            try:
                rate = _fetch_ptax(today)
            except Exception as error:
                _last_failure_at = time.monotonic()
                print(
                    f"[CUSTOS] PTAX indisponível ({type(error).__name__}); "
                    "usando a cotação de reserva.",
                    flush=True,
                )
            else:
                _cached_rate = (today, rate)
                _last_failure_at = None
                return rate

    reserve = _reserve_rate()

    if reserve is None:
        print(
            f"[CUSTOS] Sem cotação: defina {RESERVE_RATE_ENV} para converter "
            "os custos para R$.",
            flush=True,
        )

    return reserve


def cost_fields(cost_usd: float | None) -> dict:
    """Campos de custo em R$ para gravar junto do custo em US$."""
    if cost_usd is None:
        return {}

    rate = get_usd_brl_rate()

    if rate is None:
        return {"cost_usd": cost_usd}

    return {
        "cost_usd": cost_usd,
        "cost_brl": round(cost_usd * rate.rate, 4),
        "usd_brl_rate": round(rate.rate, 4),
        "rate_date": rate.rate_date.isoformat() if rate.rate_date else None,
        "rate_source": rate.source,
    }


def response_cost_usd(response) -> float | None:
    """Custo em US$ informado pelo OpenRouter na resposta do LiteLLM."""
    usage = getattr(response, "usage", None)
    cost = getattr(usage, "cost", None)

    if cost is None:
        hidden = getattr(response, "_hidden_params", None) or {}
        cost = hidden.get("response_cost")

    try:
        return float(cost) if cost is not None else None
    except (TypeError, ValueError):
        return None


# --- Custo das respostas do agente -------------------------------------------
# agent_runner aponta esta variável para o dicionário de uso da tentativa; o
# cliente do LiteLLM (services/llm_cost_client.py) soma o custo de cada
# chamada ao modelo (uma resposta pode ter várias, por causa das ferramentas).
_ATTEMPT_USAGE: ContextVar[dict | None] = ContextVar(
    "agora_attempt_usage",
    default=None,
)


def track_attempt_cost(usage: dict) -> None:
    _ATTEMPT_USAGE.set(usage)


def add_response_cost(response) -> None:
    usage = _ATTEMPT_USAGE.get()
    cost = response_cost_usd(response)

    if usage is None or cost is None:
        return

    usage["cost_usd"] = usage.get("cost_usd", 0.0) + cost


# --- Chamadas avulsas (título, LGPD, ficha, embeddings) -----------------------
_AUX_USAGE: ContextVar[list[dict] | None] = ContextVar(
    "agora_aux_usage",
    default=None,
)


@contextmanager
def collect_usage() -> Iterator[list[dict]]:
    """Junta o uso das chamadas avulsas feitas dentro do bloco, para quem
    chamou gravar em ai_usage com o próprio login."""
    collected: list[dict] = []
    token = _AUX_USAGE.set(collected)

    try:
        yield collected
    finally:
        _AUX_USAGE.reset(token)


def note_usage(
    purpose: str,
    model: str,
    response,
) -> None:
    """Anota o uso de uma chamada avulsa (sem efeito fora de collect_usage)."""
    usage = getattr(response, "usage", None)
    note_usage_values(
        purpose=purpose,
        model=model,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        cost_usd=response_cost_usd(response),
    )


def note_usage_values(
    purpose: str,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cost_usd: float | None,
) -> None:
    """Como note_usage, para chamadas que não devolvem o custo (o custo é
    calculado por quem chama, ex.: embeddings)."""
    collected = _AUX_USAGE.get()

    if collected is None:
        return

    collected.append(
        {
            "purpose": purpose,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost_usd,
        }
    )
