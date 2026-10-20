"""Aba "Usuários" do painel do TI: cadastro de novas contas sem e-mail.

O TI informa nome e e-mail; a senha temporária é gerada (ou digitada pelo
TI). A conta nasce confirmada, sem convite por e-mail, e no primeiro login a
pessoa cai na mesma tela "Crie sua nova senha" do reset (admin/senhas.py).
"""

import re

import streamlit as st
from supabase import Client

from admin import supabase_admin
from admin.cache import clear_user_emails, user_emails
from admin.senhas import (
    MIN_PASSWORD_LENGTH,
    format_date,
    generate_temporary_password,
    show_temporary_password,
)
from database.operations_team import (
    list_operations_team,
    remove_from_operations,
    set_operations_level,
)
from database.password_resets import require_password_change
from database.roles import LEVEL_COORDINATOR, LEVEL_OPERATOR
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
TI_ROLE_STATE_KEY = "admin_ti_role_changed"
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

    clear_user_emails()
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

    _show_ti_section(client)
    st.divider()
    _show_operations_section(client)
    st.divider()
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
        emails = user_emails(client)
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


def _show_ti_section(client: Client) -> None:
    """Quem acessa o Painel do TI. Antes só pelo SQL Editor do Supabase."""
    changed = st.session_state.pop(TI_ROLE_STATE_KEY, None)

    if changed:
        st.success(changed)

    st.subheader("Equipe de TI")
    st.caption(
        "Quem está aqui vê o Painel de controle: gastos, senhas, usuários, base de "
        "conhecimento e modelos. A mudança vale no próximo login da pessoa."
    )

    try:
        emails = user_emails(client)
        ti_ids = supabase_admin.list_ti_user_ids(client)
    except Exception as error:
        print(f"[TI] Falha ao listar a equipe de TI: {type(error).__name__}", flush=True)
        st.error("Não foi possível carregar a equipe de TI.")
        return

    st.markdown(
        "\n".join(
            f"- {emails.get(user_id, user_id)}"
            for user_id in sorted(ti_ids, key=lambda user_id: emails.get(user_id, user_id).lower())
        )
        or "Ninguém no TI."
    )

    own_id = str(st.session_state.get("user_id") or "")
    user_ids = sorted(
        (user_id for user_id in emails if user_id != own_id),
        key=lambda user_id: emails[user_id].lower(),
    )

    if not user_ids:
        return

    with st.form("admin_ti_role_form", border=True):
        target_user_id = st.selectbox(
            "Usuário",
            options=user_ids,
            format_func=lambda user_id: emails[user_id] + (" (TI)" if user_id in ti_ids else ""),
            index=None,
            placeholder="Escolha o usuário",
        )
        grant_column, revoke_column = st.columns(2)

        with grant_column:
            grant = st.form_submit_button(
                "Incluir no TI",
                icon=":material/admin_panel_settings:",
                use_container_width=True,
            )

        with revoke_column:
            revoke = st.form_submit_button(
                "Retirar do TI",
                icon=":material/remove_moderator:",
                use_container_width=True,
            )

    if not (grant or revoke):
        return

    if target_user_id is None:
        st.warning("Escolha o usuário.")
        return

    try:
        supabase_admin.set_ti_role(
            requester=client,
            target_user_id=target_user_id,
            grant=bool(grant),
        )
    except supabase_admin.CannotRemoveOwnTiRoleError:
        st.warning("Você não pode retirar o próprio acesso.")
        return
    except supabase_admin.LastTiError:
        st.warning("A ÁGORA precisa de pelo menos uma pessoa no TI.")
        return
    except supabase_admin.NotAuthorizedError:
        st.error("Acesso restrito ao TI.")
        return
    except Exception as error:
        print(f"[TI] Falha ao mudar o papel TI: {type(error).__name__}", flush=True)
        st.error("Não foi possível salvar. Tente novamente.")
        return

    st.session_state[TI_ROLE_STATE_KEY] = (
        f"**{emails[target_user_id]}** "
        + ("incluído(a) no TI." if grant else "retirado(a) do TI.")
    )
    st.rerun()


OPERATIONS_STATE_KEY = "admin_operations_team_changed"
OPERATIONS_LEVEL_LABELS = {
    LEVEL_COORDINATOR: "Coordenador(a)",
    LEVEL_OPERATOR: "Operadora",
}


def _show_operations_section(client: Client) -> None:
    """Equipe de Operações (migração 015): coordenadores cadastram
    fornecedores no Painel; operadoras recebem os fornecedores pela
    conversa com a ÁGORA."""
    changed = st.session_state.pop(OPERATIONS_STATE_KEY, None)

    if changed:
        st.success(changed)

    st.subheader("Equipe de Operações")
    st.caption(
        "Coordenadores cadastram, editam e retiram fornecedores (veem só a "
        "aba Fornecedores do Painel). Operadoras recebem os fornecedores "
        "cadastrados pela conversa com a ÁGORA. Quem não está aqui não vê os "
        "fornecedores. A mudança vale em até 5 minutos (no Painel, no "
        "próximo login)."
    )

    try:
        emails = user_emails(client)
        team = list_operations_team(client)
    except Exception as error:
        print(f"[TI] Falha ao listar a equipe de Operações: {type(error).__name__}", flush=True)
        st.error("Não foi possível carregar a equipe de Operações. Confira se a migração 015 foi aplicada.")
        return

    for level, label in OPERATIONS_LEVEL_LABELS.items():
        members = sorted(
            (emails.get(user_id, user_id) for user_id, member_level in team.items() if member_level == level),
            key=str.lower,
        )
        st.markdown(f"**{label}s:** " + (", ".join(members) if members else "ninguém"))

    user_ids = sorted(emails, key=lambda user_id: emails[user_id].lower())

    if not user_ids:
        return

    with st.form("admin_operations_team_form", border=True):
        target_user_id = st.selectbox(
            "Usuário",
            options=user_ids,
            format_func=lambda user_id: emails[user_id] + (
                f" ({OPERATIONS_LEVEL_LABELS[team[user_id]]})" if user_id in team else ""
            ),
            index=None,
            placeholder="Escolha o usuário",
        )
        level = st.radio(
            "Nível",
            options=list(OPERATIONS_LEVEL_LABELS),
            format_func=OPERATIONS_LEVEL_LABELS.get,
            horizontal=True,
        )
        save_column, remove_column = st.columns(2)

        with save_column:
            save = st.form_submit_button(
                "Incluir ou mudar o nível",
                icon=":material/group_add:",
                use_container_width=True,
            )

        with remove_column:
            remove = st.form_submit_button(
                "Retirar de Operações",
                icon=":material/group_remove:",
                use_container_width=True,
            )

    if not (save or remove):
        return

    if target_user_id is None:
        st.warning("Escolha o usuário.")
        return

    try:
        if save:
            set_operations_level(
                client=client,
                user_id=target_user_id,
                level=level,
                changed_by=str(st.session_state.get("user_id") or ""),
            )
            message = f"**{emails[target_user_id]}** agora é {OPERATIONS_LEVEL_LABELS[level].lower()} em Operações."
        else:
            remove_from_operations(
                client=client,
                user_id=target_user_id,
            )
            message = f"**{emails[target_user_id]}** retirado(a) de Operações."
    except Exception as error:
        print(f"[TI] Falha ao mudar a equipe de Operações: {type(error).__name__}", flush=True)
        st.error("Não foi possível salvar. Tente novamente.")
        return

    st.session_state[OPERATIONS_STATE_KEY] = message
    st.rerun()


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
        emails = user_emails(client)
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

    clear_user_emails()
    st.session_state[DELETED_USER_STATE_KEY] = full_name or email
    st.rerun()
