"""Painel do TI: botão na barra lateral e tela com as abas de administração.

O papel é conferido no banco (database.roles.is_ti) antes de mostrar
qualquer coisa; as políticas RLS também bloqueiam quem não é TI.
"""

import streamlit as st
from supabase import Client

from admin.base_conhecimento import show_knowledge_tab
from admin.gastos import show_costs_tab
from admin.modelos_prompt import show_templates_tab
from admin.senhas import show_passwords_tab
from admin.usuarios import show_users_tab
from database.roles import is_ti

PANEL_STATE_KEY = "admin_panel_open"
ROLE_CACHE_KEY = "is_ti"


def user_is_ti(client: Client) -> bool:
    """Papel TI da sessão (consultado uma vez; o painel confere de novo)."""
    if ROLE_CACHE_KEY not in st.session_state:
        st.session_state[ROLE_CACHE_KEY] = is_ti(client)

    return bool(st.session_state[ROLE_CACHE_KEY])


def show_admin_sidebar_button(client: Client) -> None:
    if not user_is_ti(client):
        return

    if st.button(
        "Painel do TI",
        icon=":material/admin_panel_settings:",
        use_container_width=True,
        key="open_admin_panel",
    ):
        st.session_state[PANEL_STATE_KEY] = True
        st.rerun()


def close_admin_panel() -> None:
    st.session_state[PANEL_STATE_KEY] = False


def is_admin_panel_open() -> bool:
    return bool(st.session_state.get(PANEL_STATE_KEY))


def show_admin_panel(client: Client) -> None:
    # Confere de novo no banco a cada abertura: o papel pode ter sido retirado.
    if not is_ti(client):
        st.session_state[ROLE_CACHE_KEY] = False
        close_admin_panel()
        st.error("Acesso restrito ao TI.")
        return

    header_column, back_column = st.columns(
        [5, 1],
        vertical_alignment="center",
    )

    with header_column:
        st.title("Painel do TI")

    with back_column:
        if st.button(
            "Voltar às conversas",
            icon=":material/arrow_back:",
            use_container_width=True,
            key="close_admin_panel",
        ):
            close_admin_panel()
            st.rerun()

    costs_tab, passwords_tab, users_tab, knowledge_tab, templates_tab = st.tabs(
        ["Gastos", "Senhas", "Usuários", "Base de conhecimento", "Modelos de prompt"]
    )

    with costs_tab:
        show_costs_tab(client)

    with passwords_tab:
        show_passwords_tab(client)

    with users_tab:
        show_users_tab(client)

    with knowledge_tab:
        show_knowledge_tab(client)

    with templates_tab:
        show_templates_tab(client)
