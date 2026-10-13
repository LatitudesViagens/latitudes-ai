"""Lista de e-mails das contas guardada por pouco tempo na sessão do TI.

Cada busca confere o papel TI, abre a conexão administrativa e lista as
contas no Supabase; o painel fazia isso até 5 vezes por clique (Usuários,
Senhas, Base de conhecimento). Cadastro e exclusão limpam a lista na hora.
"""

import time

import streamlit as st
from supabase import Client

from admin import supabase_admin

USER_EMAILS_CACHE_KEY = "_admin_user_emails_cache"
USER_EMAILS_CACHE_SECONDS = 60


def user_emails(client: Client) -> dict[str, str]:
    """{id do usuário: e-mail}, reaproveitado por até 60 s."""
    cached = st.session_state.get(USER_EMAILS_CACHE_KEY)

    if cached is not None and time.monotonic() - cached[0] < USER_EMAILS_CACHE_SECONDS:
        return cached[1]

    emails = supabase_admin.list_user_emails(client)
    st.session_state[USER_EMAILS_CACHE_KEY] = (time.monotonic(), emails)
    return emails


def clear_user_emails() -> None:
    st.session_state.pop(USER_EMAILS_CACHE_KEY, None)
