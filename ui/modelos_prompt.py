"""Modelos de prompt na tela de conversa vazia: a pessoa escolhe um e o
texto vai para o campo de mensagem, para completar e enviar."""

from collections.abc import Callable

import streamlit as st
from supabase import Client

from database.prompt_templates import list_active_templates

TEMPLATES_PER_ROW = 3


def show_template_picker(
    client: Client,
    on_choose: Callable[[str], None],
) -> None:
    try:
        templates = list_active_templates(client)
    except Exception as error:
        print(f"[MODELOS] Falha ao carregar: {type(error).__name__}", flush=True)
        return

    if not templates:
        return

    st.html('<div class="prompt-templates-title">COMECE POR UM MODELO</div>')

    for start in range(0, len(templates), TEMPLATES_PER_ROW):
        row = templates[start:start + TEMPLATES_PER_ROW]
        # Colunas vazias dos lados centralizam linhas com menos modelos.
        side_width = (TEMPLATES_PER_ROW - len(row)) / 2 + 0.5
        columns = st.columns(
            [side_width, *([1] * len(row)), side_width],
        )[1:-1]

        for column, template in zip(columns, row):
            with column:
                if st.button(
                    template["title"],
                    key=f"use_template_{template['id']}",
                    help=template.get("description") or None,
                    use_container_width=True,
                ):
                    on_choose(str(template["content"]))
                    st.rerun()
