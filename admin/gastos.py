"""Aba "Gastos" do painel do TI: total do período e custo por modelo.

Soma as respostas (message_attempts) e as chamadas avulsas (ai_usage). As
políticas RLS só liberam as linhas de todos para quem tem o papel TI.
"""

from datetime import datetime, time, timedelta

import pandas as pd
import streamlit as st
from supabase import Client

from services.custos import SAO_PAULO, get_usd_brl_rate

PAGE_SIZE = 1000

ATTEMPT_COLUMNS = "created_at,model,simulated,prompt_tokens,completion_tokens,cost_usd,cost_brl"
AI_USAGE_COLUMNS = "created_at,model,prompt_tokens,completion_tokens,cost_usd,cost_brl"


def _fetch_rows(
    client: Client,
    table: str,
    columns: str,
    start: datetime,
    end: datetime,
) -> list[dict]:
    rows: list[dict] = []
    offset = 0

    while True:
        response = (
            client.table(table)
            .select(columns)
            .gte("created_at", start.isoformat())
            .lt("created_at", end.isoformat())
            .order("created_at")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)

        if len(batch) < PAGE_SIZE:
            return rows

        offset += PAGE_SIZE


def summarize_costs(
    attempts: list[dict],
    ai_usage: list[dict],
    current_rate: float | None,
) -> dict:
    """Total do período e custo por modelo.

    Chamadas sem custo (registradas antes do controle de custos) entram na
    contagem "without_cost" e não somam valor. Chamadas com US$ mas sem R$
    são convertidas pela cotação atual. Tentativas simuladas ficam de fora.
    """
    records = [
        row
        for row in attempts
        if not row.get("simulated")
    ] + list(ai_usage)

    if not records:
        return {"empty": True}

    frame = pd.DataFrame(records)

    for column in ("prompt_tokens", "completion_tokens", "cost_usd", "cost_brl"):
        if column not in frame:
            frame[column] = None
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    without_cost = int(frame["cost_usd"].isna().sum())
    missing_brl = frame["cost_brl"].isna() & frame["cost_usd"].notna()

    if current_rate is not None:
        frame.loc[missing_brl, "cost_brl"] = (
            frame.loc[missing_brl, "cost_usd"] * current_rate
        )

    numeric_columns = ["prompt_tokens", "completion_tokens", "cost_usd", "cost_brl"]
    frame[numeric_columns] = frame[numeric_columns].fillna(0)
    frame["tokens"] = frame["prompt_tokens"] + frame["completion_tokens"]
    frame["modelo"] = frame["model"].astype(str).str.removeprefix("openrouter/")

    by_model = (
        frame.groupby("modelo")
        .agg(
            chamadas=("model", "size"),
            tokens=("tokens", "sum"),
            custo_brl=("cost_brl", "sum"),
            custo_usd=("cost_usd", "sum"),
        )
        .reset_index()
        .sort_values("custo_brl", ascending=False)
    )

    return {
        "empty": False,
        "total_brl": float(frame["cost_brl"].sum()),
        "total_usd": float(frame["cost_usd"].sum()),
        "calls": int(len(frame)),
        "without_cost": without_cost,
        "by_model": by_model,
    }


def _format_number(value: float, decimals: int) -> str:
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def format_brl(value: float) -> str:
    if 0 < value < 0.01:
        return "menos de R$ 0,01"

    return f"R$ {_format_number(value, 2)}"


def format_usd(value: float) -> str:
    if 0 < value < 0.01:
        return "menos de US$ 0,01"

    return f"US$ {_format_number(value, 2)}"


def show_costs_tab(client: Client) -> None:
    today = datetime.now(SAO_PAULO).date()
    period = st.date_input(
        "Período",
        value=(today.replace(day=1), today),
        max_value=today,
        format="DD/MM/YYYY",
        key="admin_costs_period",
    )

    if not isinstance(period, tuple) or len(period) != 2:
        st.caption("Escolha a data inicial e a final.")
        return

    start_day, end_day = period
    start = datetime.combine(start_day, time.min, tzinfo=SAO_PAULO)
    end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=SAO_PAULO)

    try:
        attempts = _fetch_rows(
            client=client,
            table="message_attempts",
            columns=ATTEMPT_COLUMNS,
            start=start,
            end=end,
        )
        ai_usage = _fetch_rows(
            client=client,
            table="ai_usage",
            columns=AI_USAGE_COLUMNS,
            start=start,
            end=end,
        )
    except Exception as error:
        print(f"[TI] Falha ao carregar gastos: {type(error).__name__}", flush=True)
        st.error("Não foi possível carregar os gastos agora.")
        return

    rate = get_usd_brl_rate()
    summary = summarize_costs(
        attempts=attempts,
        ai_usage=ai_usage,
        current_rate=rate.rate if rate else None,
    )

    if summary["empty"]:
        st.info("Nenhum uso de IA registrado nesse período.")
        return

    with st.container(border=True):
        total_column, usd_column, calls_column = st.columns(3)
        total_column.metric("Gasto total", format_brl(summary["total_brl"]))
        usd_column.metric("Em dólar", format_usd(summary["total_usd"]))
        calls_column.metric("Chamadas à IA", _format_number(summary["calls"], 0))

    notes = []

    if rate is not None:
        origin = "PTAX do Banco Central" if rate.source == "ptax" else "cotação de reserva"
        notes.append(f"Dólar a R$ {_format_number(rate.rate, 4)} ({origin}).")

    if summary["without_cost"]:
        notes.append(
            f"{_format_number(summary['without_cost'], 0)} chamada(s) foram "
            "feitas antes do controle de custos e aparecem sem valor."
        )

    if notes:
        st.caption(" ".join(notes))

    st.subheader("Custo por modelo")
    table = pd.DataFrame(
        {
            "Modelo": summary["by_model"]["modelo"],
            "Chamadas": summary["by_model"]["chamadas"].map(
                lambda value: _format_number(value, 0)
            ),
            "Tokens": summary["by_model"]["tokens"].map(
                lambda value: _format_number(value, 0)
            ),
            "Custo (R$)": summary["by_model"]["custo_brl"].map(format_brl),
            "Custo (US$)": summary["by_model"]["custo_usd"].map(format_usd),
        }
    )
    st.dataframe(
        table,
        hide_index=True,
        use_container_width=True,
    )
