"""Busca de conversas na barra lateral (só nas conversas da própria pessoa)."""

from collections.abc import Callable
import re

import streamlit as st
from supabase import Client

from database.search import search_conversations

SEARCH_INPUT_KEY = "conversation_search"
CLEAR_SEARCH_KEY = "conversation_search_clear"
MIN_SEARCH_LENGTH = 2
MAX_SNIPPET_CHARACTERS = 160

# Só os destaques (**termo**) da busca viram negrito; o resto do trecho é
# mostrado como texto, sem interpretar Markdown da conversa.
_MARKDOWN_CHARACTERS = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~$<])")


def _snippet_markdown(snippet: str) -> str:
    text = " ".join(str(snippet or "").split())

    if len(text) > MAX_SNIPPET_CHARACTERS:
        text = text[:MAX_SNIPPET_CHARACTERS].rstrip() + "…"

    parts = text.split("**")

    def escape(part: str) -> str:
        return _MARKDOWN_CHARACTERS.sub(r"\\\1", part)

    return "".join(
        f"**{escape(part)}**" if index % 2 == 1 and part else escape(part)
        for index, part in enumerate(parts)
    )


def _clear_search() -> None:
    st.session_state[SEARCH_INPUT_KEY] = ""


def show_conversation_search(
    client: Client,
    on_select: Callable[[str], None],
) -> bool:
    """Campo de busca e resultados. on_select abre a conversa escolhida.

    Retorna True enquanto há uma busca na tela: a lista de conversas some e
    volta quando o campo é apagado (ou em "Limpar busca").
    """
    if st.session_state.pop(CLEAR_SEARCH_KEY, False):
        st.session_state[SEARCH_INPUT_KEY] = ""

    search_text = st.text_input(
        "Buscar conversas",
        placeholder="Buscar nas suas conversas",
        key=SEARCH_INPUT_KEY,
        label_visibility="collapsed",
        icon=":material/search:",
    ).strip()

    if len(search_text) < MIN_SEARCH_LENGTH:
        return False

    st.button(
        "Limpar busca e ver todas as conversas",
        icon=":material/close:",
        key="clear_conversation_search",
        type="tertiary",
        on_click=_clear_search,
    )

    try:
        results = search_conversations(
            client=client,
            search_text=search_text,
        )
    except Exception as error:
        print(f"[BUSCA] Falha na busca: {type(error).__name__}", flush=True)
        st.caption("Não foi possível buscar agora.")
        return True

    if not results:
        st.caption("Nenhuma conversa encontrada.")
        return True

    st.caption(f"RESULTADOS ({len(results)})")

    for result in results:
        conversation_id = str(result["conversation_id"])

        if st.button(
            str(result.get("title") or "Conversa"),
            key=f"search_result_{conversation_id}",
            use_container_width=True,
        ):
            st.session_state[CLEAR_SEARCH_KEY] = True
            on_select(conversation_id)
            st.rerun()

        snippet = _snippet_markdown(result.get("snippet", ""))

        if snippet:
            st.caption(snippet)

    return True
