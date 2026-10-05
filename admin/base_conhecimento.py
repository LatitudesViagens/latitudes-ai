"""Base de conhecimento na interface.

- Aba do painel do TI: aprovar, editar ou rejeitar sugestões, editar ou
  retirar entradas aprovadas e criar entradas novas.
- Botão "Sugerir para a base", na resposta da conversa (qualquer pessoa).

Nada entra na base sem aprovação do TI. O embedding (e o LiteLLM) só é
carregado quando o TI salva algo, para não deixar a tela mais lenta.
"""

import traceback

import streamlit as st
from supabase import Client

from admin import supabase_admin
from database.ai_usage import log_ai_usage
from database.knowledge_entries import (
    STATUS_APPROVED,
    STATUS_PENDING,
    approve_entry,
    create_approved_entry,
    find_suggestion_for_message,
    list_entries,
    reject_entry,
    suggest_entry,
)
from services.custos import collect_usage

FLASH_KEY = "admin_knowledge_flash"


def _embedding_for(
    client: Client,
    question: str,
) -> tuple[str, str]:
    from services.embeddings import EMBEDDING_MODEL, embed_text, vector_literal

    with collect_usage() as usages:
        vector = embed_text(question)

    log_ai_usage(
        client=client,
        usages=usages,
    )

    return vector_literal(vector), EMBEDDING_MODEL


def _forget_base_state() -> None:
    from services.base_conhecimento import forget_base_state

    forget_base_state()


def _save_approved(
    client: Client,
    entry_id: str | None,
    question: str,
    answer: str,
) -> bool:
    if not question.strip() or not answer.strip():
        st.warning("Preencha a pergunta e a resposta.")
        return False

    try:
        embedding, model = _embedding_for(
            client=client,
            question=question,
        )

        if entry_id is None:
            create_approved_entry(
                client=client,
                question=question,
                answer=answer,
                embedding=embedding,
                embedding_model=model,
                reviewer_id=st.session_state.user_id,
            )
        else:
            approve_entry(
                client=client,
                entry_id=entry_id,
                question=question,
                answer=answer,
                embedding=embedding,
                embedding_model=model,
                reviewer_id=st.session_state.user_id,
            )
    except Exception:
        traceback.print_exc()
        st.error("Não foi possível salvar. Tente novamente.")
        return False

    _forget_base_state()
    return True


def _reject(
    client: Client,
    entry_id: str,
    note: str | None = None,
) -> bool:
    try:
        reject_entry(
            client=client,
            entry_id=entry_id,
            reviewer_id=st.session_state.user_id,
            note=note,
        )
    except Exception:
        traceback.print_exc()
        st.error("Não foi possível concluir. Tente novamente.")
        return False

    _forget_base_state()
    return True


def _emails(client: Client) -> dict[str, str]:
    if not supabase_admin.is_configured():
        return {}

    try:
        return supabase_admin.list_user_emails(client)
    except Exception as error:
        print(f"[TI] Falha ao listar e-mails: {type(error).__name__}", flush=True)
        return {}


def _flash(message: str) -> None:
    st.session_state[FLASH_KEY] = message
    st.rerun()


def show_knowledge_tab(client: Client) -> None:
    message = st.session_state.pop(FLASH_KEY, None)

    if message:
        st.success(message)

    try:
        pending = list_entries(client=client, status=STATUS_PENDING)
        approved = list_entries(client=client, status=STATUS_APPROVED)
    except Exception as error:
        print(f"[TI] Falha ao carregar a base: {type(error).__name__}", flush=True)
        st.error("Não foi possível carregar a base de conhecimento.")
        return

    emails = _emails(client)

    st.subheader(f"Sugestões aguardando aprovação ({len(pending)})")
    st.caption(
        "Revise antes de aprovar: a resposta aprovada pode ser enviada a "
        "qualquer pessoa que fizer uma pergunta parecida."
    )

    if not pending:
        st.caption("Nenhuma sugestão pendente.")

    for entry in pending:
        entry_id = entry["id"]
        author = emails.get(str(entry.get("suggested_by")), "")

        with st.expander(entry["question"][:90], expanded=False):
            if author:
                st.caption(f"Sugerida por {author}")

            question = st.text_area(
                "Pergunta",
                value=entry["question"],
                key=f"kb_pending_question_{entry_id}",
            )
            answer = st.text_area(
                "Resposta",
                value=entry["answer"],
                height=220,
                key=f"kb_pending_answer_{entry_id}",
            )
            approve_column, reject_column = st.columns(2)

            if approve_column.button(
                "Aprovar",
                icon=":material/check:",
                type="primary",
                use_container_width=True,
                key=f"kb_approve_{entry_id}",
            ) and _save_approved(
                client=client,
                entry_id=entry_id,
                question=question,
                answer=answer,
            ):
                _flash("Sugestão aprovada e incluída na base.")

            if reject_column.button(
                "Rejeitar",
                icon=":material/close:",
                use_container_width=True,
                key=f"kb_reject_{entry_id}",
            ) and _reject(client=client, entry_id=entry_id):
                _flash("Sugestão rejeitada. Ela não será usada.")

    st.subheader(f"Na base ({len(approved)})")

    if not approved:
        st.caption("Nenhuma resposta aprovada ainda.")

    for entry in approved:
        entry_id = entry["id"]

        with st.expander(entry["question"][:90], expanded=False):
            question = st.text_area(
                "Pergunta",
                value=entry["question"],
                key=f"kb_approved_question_{entry_id}",
            )
            answer = st.text_area(
                "Resposta",
                value=entry["answer"],
                height=220,
                key=f"kb_approved_answer_{entry_id}",
            )
            save_column, remove_column = st.columns(2)

            if save_column.button(
                "Salvar alterações",
                icon=":material/save:",
                use_container_width=True,
                key=f"kb_save_{entry_id}",
            ) and _save_approved(
                client=client,
                entry_id=entry_id,
                question=question,
                answer=answer,
            ):
                _flash("Alterações salvas.")

            if remove_column.button(
                "Retirar da base",
                icon=":material/delete:",
                use_container_width=True,
                key=f"kb_remove_{entry_id}",
            ) and _reject(
                client=client,
                entry_id=entry_id,
                note="Retirada da base pelo TI.",
            ):
                _flash("Resposta retirada da base.")

    st.subheader("Nova resposta")

    with st.form("kb_new_entry_form", clear_on_submit=True, border=True):
        question = st.text_area("Pergunta")
        answer = st.text_area("Resposta", height=200)
        submitted = st.form_submit_button(
            "Incluir na base",
            type="primary",
        )

    if submitted and _save_approved(
        client=client,
        entry_id=None,
        question=question,
        answer=answer,
    ):
        _flash("Resposta incluída na base.")


# --- Botão na conversa ----------------------------------------------------------
def _suggestion_state_key(message_id: str) -> str:
    return f"kb_suggestion_{message_id}"


def show_suggestion_button(
    client: Client,
    message: dict,
    question: str,
) -> None:
    """Botão para sugerir uma resposta da conversa à base (vai para o TI).

    Respostas com dados de clientes são bloqueadas (mesma verificação da
    memória coletiva, LGPD).
    """
    message_id = str(message["id"])
    state_key = _suggestion_state_key(message_id)

    if state_key not in st.session_state:
        try:
            st.session_state[state_key] = find_suggestion_for_message(
                client=client,
                source_message_id=message_id,
            ) is not None
        except Exception:
            st.session_state[state_key] = False

    if st.session_state[state_key]:
        st.caption("Sugerida para a base de conhecimento.")
        return

    if not st.button(
        "Sugerir para a base de conhecimento",
        icon=":material/lightbulb:",
        type="tertiary",
        key=f"kb_suggest_{message_id}",
        help="O TI revisa antes de a resposta ser usada para outras pessoas.",
    ):
        return

    from services.personal_data_check import find_client_data

    answer = str(message.get("content", ""))

    with (
        collect_usage() as usages,
        st.spinner("Verificando dados de clientes..."),
    ):
        try:
            check = find_client_data(f"{question}\n\n{answer}")
        except Exception:
            traceback.print_exc()
            check = None

    log_ai_usage(
        client=client,
        usages=usages,
        conversation_id=message.get("conversation_id"),
    )

    if check is None or check.found is None:
        st.error(
            "Não foi possível verificar dados de clientes agora. Tente de "
            "novo em instantes."
        )
        return

    if check.found:
        st.warning(
            "Esta resposta tem dados de clientes e não pode ir para a base "
            "de conhecimento (LGPD)."
        )
        return

    try:
        suggest_entry(
            client=client,
            question=question,
            answer=answer,
            source_message_id=message_id,
        )
    except Exception:
        traceback.print_exc()
        st.error("Não foi possível enviar a sugestão. Tente novamente.")
        return

    st.session_state[state_key] = True
    st.toast("Sugestão enviada ao TI para aprovação.")
    st.rerun()
