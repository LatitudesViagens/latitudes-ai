"""Aba "Usuários" do painel do TI: cadastro de novas contas sem e-mail.

O TI informa nome e e-mail; a senha temporária é gerada (ou digitada pelo
TI). A conta nasce confirmada, sem convite por e-mail, e no primeiro login a
pessoa cai na mesma tela "Crie sua nova senha" do reset (admin/senhas.py).
"""

import re

import streamlit as st
from supabase import Client

from admin import supabase_admin
from admin.senhas import (
    MIN_PASSWORD_LENGTH,
    format_date,
    generate_temporary_password,
    show_temporary_password,
)
from database.password_resets import require_password_change
from database.user_deletions import (
    list_user_deletions,
    log_user_deletion,
)
from database.user_registrations import (
    list_user_registrations,
    log_user_registration,
)

CREATED_USER_STATE_KEY = "admin_created_user"
DELETED_USER_STATE_KEY = "admin_deleted_user"
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _register_user(
    client: Client,
    full_name: str,
    email: str,
    typed_password: str,
) -> None:
    full_name = full_name.strip()
    email = email.strip().lower()

    if not full_name:
        st.warning("Preencha o nome.")
        return

    if not _EMAIL_PATTERN.match(email):
        st.warning("Preencha um e-mail válido.")
        return

    if typed_password and len(typed_password) < MIN_PASSWORD_LENGTH:
        st.warning(
            f"A senha temporária precisa ter pelo menos {MIN_PASSWORD_LENGTH} "
            "caracteres (ou deixe em branco para gerar uma)."
        )
        return

    temporary_password = typed_password or generate_temporary_password()

    try:
        user_id = supabase_admin.create_user_with_temporary_password(
            requester=client,
            email=email,
            full_name=full_name,
            temporary_password=temporary_password,
        )
    except supabase_admin.EmailAlreadyRegisteredError:
        st.warning(
            "Já existe uma conta com esse e-mail. Para trocar a senha dessa "
            "pessoa, use a aba Senhas."
        )
        return
    except supabase_admin.NotAuthorizedError:
        st.error("Acesso restrito ao TI.")
        return
    except Exception as error:
        print(
            f"[TI] Falha ao cadastrar usuário: {type(error).__name__}",
            flush=True,
        )
        st.error(
            "Não foi possível cadastrar. Confira o e-mail e a senha temporária "
            "e tente novamente."
        )
        return

    try:
        log_user_registration(
            client=client,
            user_id=user_id,
            full_name=full_name,
            email=email,
        )
        require_password_change(
            client=client,
            user_id=user_id,
            reset_id=None,
        )
    except Exception as error:
        print(
            f"[TI] Falha ao registrar cadastro: {type(error).__name__}",
            flush=True,
        )
        st.warning(
            "A conta foi criada, mas não foi possível registrar o cadastro nem "
            "obrigar a troca de senha no primeiro acesso. Peça para a pessoa "
            "trocar a senha assim que entrar."
        )
        show_temporary_password(
            message=f"Conta de **{full_name}** ({email}) criada.",
            password=temporary_password,
        )
        return

    st.session_state[CREATED_USER_STATE_KEY] = (
        full_name,
        email,
        temporary_password,
    )
    st.rerun()


def show_users_tab(client: Client) -> None:
    if not supabase_admin.is_configured():
        st.warning(
            "Para cadastrar usuários, configure SUPABASE_SERVICE_ROLE_KEY no "
            "servidor (Supabase → Project Settings → API → service_role)."
        )
        return

    created = st.session_state.pop(CREATED_USER_STATE_KEY, None)

    if created:
        full_name, email, password = created
        show_temporary_password(
            message=(
                f"Conta de **{full_name}** ({email}) criada. Repasse a senha "
                "temporária à pessoa: ela aparece só agora. No primeiro acesso, "
                "a pessoa vai criar uma senha nova."
            ),
            password=password,
        )

    st.subheader("Cadastrar usuário")
    st.caption(
        "A conta já nasce confirmada e nenhum e-mail é enviado. A pessoa entra "
        "com a senha temporária e é obrigada a criar outra na hora."
    )

    with st.form("admin_create_user_form", clear_on_submit=True, border=True):
        full_name = st.text_input("Nome")
        # autocomplete: impede o navegador de preencher com a senha salva de
        # quem está logado (o campo ficaria "preenchido" sem a pessoa ver).
        email = st.text_input(
            "E-mail",
            placeholder="nome@latitudes.com.br",
            autocomplete="off",
        )
        typed_password = st.text_input(
            "Senha temporária (opcional)",
            type="password",
            placeholder="Deixe em branco para gerar automaticamente",
            autocomplete="new-password",
        )
        submitted = st.form_submit_button(
            "Cadastrar usuário",
            type="primary",
        )

    if submitted:
        _register_user(
            client=client,
            full_name=full_name,
            email=email,
            typed_password=typed_password,
        )

    _show_delete_section(client)

    st.subheader("Histórico de cadastros")

    try:
        registrations = list_user_registrations(client=client)
    except Exception as error:
        print(
            f"[TI] Falha ao listar cadastros: {type(error).__name__}",
            flush=True,
        )
        st.error("Não foi possível carregar o histórico.")
        return

    if not registrations:
        st.caption("Nenhum cadastro feito pelo painel ainda.")
        return

    try:
        emails = supabase_admin.list_user_emails(client)
    except Exception:
        emails = {}

    st.dataframe(
        [
            {
                "Quando": format_date(registration["created_at"]),
                "Nome": registration["full_name"],
                "E-mail": registration["email"],
                "Cadastrado por": emails.get(
                    str(registration["created_by"]),
                    "—",
                ),
            }
            for registration in registrations
        ],
        hide_index=True,
        use_container_width=True,
    )


def _show_delete_section(client: Client) -> None:
    """Excluir conta: apaga a conta e as conversas da pessoa; roteiros
    publicados, base de conhecimento, modelos, gastos e auditoria ficam
    (migração 011)."""
    deleted = st.session_state.pop(DELETED_USER_STATE_KEY, None)

    if deleted:
        st.success(f"Conta de **{deleted}** excluída.")

    st.subheader("Excluir usuário")
    st.caption(
        "Apaga a conta, as conversas e os arquivos da pessoa. Continuam na "
        "ÁGORA: roteiros que ela publicou na memória coletiva, respostas da "
        "base de conhecimento, modelos de prompt e o histórico de gastos. "
        "Não dá para desfazer."
    )

    try:
        emails = supabase_admin.list_user_emails(client)
    except Exception as error:
        print(f"[TI] Falha ao listar usuários: {type(error).__name__}", flush=True)
        st.error("Não foi possível carregar a lista de usuários.")
        return

    own_id = str(st.session_state.get("user_id") or "")
    user_ids = sorted(
        (user_id for user_id in emails if user_id != own_id),
        key=lambda user_id: emails[user_id].lower(),
    )

    if not user_ids:
        st.caption("Nenhum outro usuário cadastrado.")
        return

    with st.form("admin_delete_user_form", clear_on_submit=True, border=True):
        target_user_id = st.selectbox(
            "Usuário",
            options=user_ids,
            format_func=lambda user_id: emails[user_id],
            index=None,
            placeholder="Escolha o usuário",
        )
        confirmation = st.text_input(
            "Para confirmar, digite o e-mail do usuário",
            autocomplete="off",
        )
        submitted = st.form_submit_button(
            "Excluir usuário",
            icon=":material/delete:",
        )

    if submitted:
        _delete_user(
            client=client,
            target_user_id=target_user_id,
            target_email=emails.get(str(target_user_id), ""),
            confirmation=confirmation,
        )

    try:
        deletions = list_user_deletions(client=client)
    except Exception as error:
        print(
            f"[TI] Falha ao listar exclusões: {type(error).__name__}",
            flush=True,
        )
        return

    if deletions:
        st.markdown("**Histórico de exclusões**")
        st.dataframe(
            [
                {
                    "Quando": format_date(deletion["created_at"]),
                    "Nome": deletion.get("full_name") or "—",
                    "E-mail": deletion["email"],
                    "Excluído por": emails.get(
                        str(deletion.get("deleted_by")),
                        "—",
                    ),
                }
                for deletion in deletions
            ],
            hide_index=True,
            use_container_width=True,
        )


def _delete_user(
    client: Client,
    target_user_id: str | None,
    target_email: str,
    confirmation: str,
) -> None:
    if target_user_id is None:
        st.warning("Escolha o usuário.")
        return

    if confirmation.strip().lower() != target_email.strip().lower():
        st.warning("O e-mail digitado não confere. Nada foi excluído.")
        return

    try:
        email, full_name = supabase_admin.delete_user_account(
            requester=client,
            target_user_id=target_user_id,
        )
    except supabase_admin.CannotDeleteSelfError:
        st.warning("Você não pode excluir a própria conta.")
        return
    except supabase_admin.NotAuthorizedError:
        st.error("Acesso restrito ao TI.")
        return
    except Exception as error:
        print(f"[TI] Falha ao excluir usuário: {type(error).__name__}", flush=True)
        st.error(
            "Não foi possível excluir. Confira se a migração 011 foi aplicada "
            "no Supabase e tente novamente."
        )
        return

    try:
        log_user_deletion(
            client=client,
            deleted_user_id=target_user_id,
            email=email,
            full_name=full_name,
        )
    except Exception as error:
        print(
            f"[TI] Falha ao registrar exclusão: {type(error).__name__}",
            flush=True,
        )

    st.session_state[DELETED_USER_STATE_KEY] = full_name or email
    st.rerun()
