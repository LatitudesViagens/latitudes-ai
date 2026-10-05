"""Aba "Modelos de prompt" do painel do TI: criar, editar, ativar/desativar,
ordenar e apagar os modelos que aparecem ao começar uma conversa."""

import traceback

import streamlit as st
from supabase import Client

from database.prompt_templates import (
    create_template,
    delete_template,
    list_all_templates,
    update_template,
)

FLASH_KEY = "admin_templates_flash"


def _flash(message: str) -> None:
    st.session_state[FLASH_KEY] = message
    st.rerun()


def _valid(
    title: str,
    content: str,
) -> bool:
    if not title.strip() or not content.strip():
        st.warning("Preencha o nome e o texto do modelo.")
        return False

    return True


def show_templates_tab(client: Client) -> None:
    message = st.session_state.pop(FLASH_KEY, None)

    if message:
        st.success(message)

    st.caption(
        "Os modelos ativos aparecem para todos ao começar uma conversa. Ao "
        "escolher um, o texto vai para o campo de mensagem para a pessoa "
        "completar e enviar."
    )

    try:
        templates = list_all_templates(client)
    except Exception as error:
        print(f"[TI] Falha ao carregar modelos: {type(error).__name__}", flush=True)
        st.error("Não foi possível carregar os modelos.")
        return

    st.subheader(f"Modelos ({len(templates)})")

    if not templates:
        st.caption("Nenhum modelo criado ainda.")

    for template in templates:
        template_id = template["id"]
        label = template["title"] + ("" if template["is_active"] else " (desativado)")

        with st.expander(label, expanded=False):
            with st.form(f"template_form_{template_id}", border=False):
                title = st.text_input("Nome", value=template["title"])
                description = st.text_input(
                    "Descrição curta (opcional)",
                    value=template.get("description") or "",
                )
                content = st.text_area(
                    "Texto do modelo",
                    value=template["content"],
                    height=180,
                )
                order_column, active_column = st.columns(2)
                sort_order = order_column.number_input(
                    "Ordem",
                    value=int(template.get("sort_order") or 0),
                    step=1,
                )
                is_active = active_column.toggle(
                    "Ativo",
                    value=bool(template["is_active"]),
                )
                saved = st.form_submit_button(
                    "Salvar",
                    icon=":material/save:",
                    type="primary",
                )

            if saved and _valid(title, content):
                try:
                    update_template(
                        client=client,
                        template_id=template_id,
                        editor_id=st.session_state.user_id,
                        title=title,
                        description=description,
                        content=content,
                        sort_order=sort_order,
                        is_active=is_active,
                    )
                except Exception:
                    traceback.print_exc()
                    st.error("Não foi possível salvar. Tente novamente.")
                else:
                    _flash("Modelo salvo.")

            if st.button(
                "Apagar modelo",
                icon=":material/delete:",
                type="tertiary",
                key=f"delete_template_{template_id}",
            ):
                try:
                    delete_template(
                        client=client,
                        template_id=template_id,
                    )
                except Exception:
                    traceback.print_exc()
                    st.error("Não foi possível apagar. Tente novamente.")
                else:
                    _flash("Modelo apagado.")

    st.subheader("Novo modelo")

    with st.form("new_template_form", clear_on_submit=True, border=True):
        title = st.text_input("Nome", placeholder="Ex.: Roteiro de viagem")
        description = st.text_input(
            "Descrição curta (opcional)",
            placeholder="Ex.: Monta um roteiro dia a dia",
        )
        content = st.text_area(
            "Texto do modelo",
            placeholder=(
                "Ex.: Crie um roteiro de [número] dias em [destino] para "
                "[perfil do grupo], com foco em [interesses]."
            ),
            height=180,
        )
        sort_order = st.number_input(
            "Ordem",
            value=len(templates) + 1,
            step=1,
        )
        created = st.form_submit_button(
            "Criar modelo",
            type="primary",
        )

    if created and _valid(title, content):
        try:
            create_template(
                client=client,
                title=title,
                description=description,
                content=content,
                sort_order=sort_order,
            )
        except Exception:
            traceback.print_exc()
            st.error("Não foi possível criar o modelo. Tente novamente.")
        else:
            _flash("Modelo criado.")
