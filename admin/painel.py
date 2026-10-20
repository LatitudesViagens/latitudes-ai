"""Painel de controle: botão na barra lateral e tela com as abas.

Quem vê o quê (conferido no banco antes de mostrar qualquer coisa; as
políticas RLS também bloqueiam):
- TI: todas as abas.
- Coordenadores de Operações: só a aba Fornecedores (migração 015).
- Operadoras e demais: não veem o Painel.
"""

import streamlit as st
from supabase import Client

from admin.base_conhecimento import show_knowledge_tab
from admin.fornecedores import show_suppliers_section
from admin.gastos import show_costs_tab
from admin.modelos_prompt import show_templates_tab
from admin.senhas import show_passwords_tab
from admin.usuarios import show_users_tab
from database.roles import LEVEL_COORDINATOR, is_ti, operations_level

PANEL_STATE_KEY = "admin_panel_open"
ROLE_CACHE_KEY = "is_ti"
COORDINATOR_CACHE_KEY = "is_operations_coordinator"
ROLE_CHECKED_KEY = "admin_panel_role_checked"


def user_is_ti(client: Client) -> bool:
    """Papel TI da sessão (consultado uma vez; o painel confere de novo)."""
    if ROLE_CACHE_KEY not in st.session_state:
        st.session_state[ROLE_CACHE_KEY] = is_ti(client)

    return bool(st.session_state[ROLE_CACHE_KEY])


def user_is_coordinator(client: Client) -> bool:
    """Coordenador de Operações (consultado uma vez por sessão)."""
    if COORDINATOR_CACHE_KEY not in st.session_state:
        st.session_state[COORDINATOR_CACHE_KEY] = operations_level(client) == LEVEL_COORDINATOR

    return bool(st.session_state[COORDINATOR_CACHE_KEY])


def show_admin_sidebar_button(client: Client) -> None:
    if not (user_is_ti(client) or user_is_coordinator(client)):
        return

    # on_click roda antes da tela: o clique roda a tela uma vez só (antes,
    # duas — e a tela piscava duas vezes).
    st.button(
        "Painel de controle",
        icon=":material/admin_panel_settings:",
        use_container_width=True,
        key="open_admin_panel",
        on_click=open_admin_panel,
    )


def open_admin_panel() -> None:
    st.session_state[PANEL_STATE_KEY] = True
    st.session_state.pop(ROLE_CHECKED_KEY, None)


def close_admin_panel() -> None:
    st.session_state[PANEL_STATE_KEY] = False


def is_admin_panel_open() -> bool:
    return bool(st.session_state.get(PANEL_STATE_KEY))


def show_admin_panel(client: Client) -> None:
    # Confere de novo no banco a cada abertura (o papel pode ter sido
    # retirado), não a cada clique dentro do painel. As ações administrativas
    # conferem de novo no servidor (supabase_admin.require_ti).
    if not st.session_state.get(ROLE_CHECKED_KEY):
        st.session_state[ROLE_CACHE_KEY] = is_ti(client)
        st.session_state[COORDINATOR_CACHE_KEY] = operations_level(client) == LEVEL_COORDINATOR

        if not (st.session_state[ROLE_CACHE_KEY] or st.session_state[COORDINATOR_CACHE_KEY]):
            close_admin_panel()
            st.error("Acesso restrito.")
            return

        st.session_state[ROLE_CHECKED_KEY] = True

    ti = bool(st.session_state[ROLE_CACHE_KEY])

    header_column, back_column = st.columns(
        [5, 1],
        vertical_alignment="center",
    )

    with header_column:
        st.title("Painel de controle")

    with back_column:
        st.button(
            "Voltar às conversas",
            icon=":material/arrow_back:",
            use_container_width=True,
            key="close_admin_panel",
            on_click=close_admin_panel,
        )

    # Só a aba escolhida roda: com st.tabs, as 5 abas (e as consultas de
    # todas) rodavam a cada clique em qualquer uma.
    if ti:
        sections = {
            "Gastos": show_costs_tab,
            "Senhas": show_passwords_tab,
            "Usuários": show_users_tab,
            "Base de conhecimento": show_knowledge_tab,
            "Fornecedores": show_suppliers_section,
            "Modelos de prompt": show_templates_tab,
        }
    else:
        # Coordenador de Operações: só a aba Fornecedores.
        sections = {"Fornecedores": show_suppliers_section}

    first = next(iter(sections))
    chosen = st.segmented_control(
        "Seção do painel",
        options=list(sections),
        default=first,
        key="admin_panel_section",
        label_visibility="collapsed",
    )

    if chosen not in sections:
        chosen = first

    sections[chosen](client)
