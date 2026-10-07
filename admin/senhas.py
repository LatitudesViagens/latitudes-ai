"""Aba "Senhas" do painel do TI e tela de troca obrigatória de senha.

O TI define uma senha temporária (sem e-mail); a pessoa entra com ela e é
obrigada a criar uma nova antes de usar a ÁGORA.
"""

from datetime import datetime
import secrets
import string

import streamlit as st
from supabase import Client

from admin import supabase_admin
from database.password_resets import (
    list_password_resets,
    log_password_reset,
    must_change_password,
    require_password_change,
)
from services.custos import SAO_PAULO

TEMPORARY_PASSWORD_LENGTH = 12
MIN_PASSWORD_LENGTH = 8
# Sem caracteres parecidos (0/O, 1/l/I), para ditar a senha sem erro.
_UNAMBIGUOUS_LETTERS = "".join(
    character
    for character in string.ascii_letters
    if character not in "OIl"
)
_UNAMBIGUOUS_DIGITS = "23456789"
_SYMBOLS = "#@!-"

TEMPORARY_PASSWORD_STATE_KEY = "admin_temporary_password"


def show_temporary_password(
    message: str,
    password: str,
) -> None:
    """Mostra a senha temporária em destaque, com botão de copiar (aparece
    uma única vez)."""
    with st.container(border=True, key="temporary_password_box"):
        st.success(message)
        st.markdown("**Senha temporária** (clique no ícone à direita para copiar):")
        st.code(password, language=None)
PASSWORD_CHECK_STATE_KEY = "password_change_checked"


def generate_temporary_password() -> str:
    """Senha forte com maiúscula, minúscula, número e símbolo."""
    required = [
        secrets.choice(string.ascii_uppercase.replace("O", "").replace("I", "")),
        secrets.choice(string.ascii_lowercase.replace("l", "")),
        secrets.choice(_UNAMBIGUOUS_DIGITS),
        secrets.choice(_SYMBOLS),
    ]
    alphabet = _UNAMBIGUOUS_LETTERS + _UNAMBIGUOUS_DIGITS
    remaining = [
        secrets.choice(alphabet)
        for _ in range(TEMPORARY_PASSWORD_LENGTH - len(required))
    ]
    characters = required + remaining
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def format_date(value: str) -> str:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)

    return moment.astimezone(SAO_PAULO).strftime("%d/%m/%Y %H:%M")


def show_passwords_tab(client: Client) -> None:
    if not supabase_admin.is_configured():
        st.warning(
            "Para redefinir senhas, configure SUPABASE_SERVICE_ROLE_KEY no "
            "servidor (Supabase → Project Settings → API → service_role)."
        )
        return

    try:
        emails = supabase_admin.list_user_emails(client)
    except Exception as error:
        print(f"[TI] Falha ao listar usuários: {type(error).__name__}", flush=True)
        st.error("Não foi possível carregar a lista de usuários.")
        return

    created = st.session_state.pop(TEMPORARY_PASSWORD_STATE_KEY, None)

    if created:
        email, password = created
        show_temporary_password(
            message=(
                f"Senha temporária de **{email}** definida. Copie e repasse à "
                "pessoa: ela aparece só agora. No próximo acesso, a pessoa vai "
                "criar uma senha nova."
            ),
            password=password,
        )

    st.subheader("Definir senha temporária")
    st.caption(
        "Use quando alguém esquecer a senha. A pessoa entra com a senha "
        "temporária e é obrigada a criar outra na hora."
    )

    user_ids = sorted(emails, key=lambda user_id: emails[user_id].lower())

    with st.form("admin_reset_password_form", border=True):
        target_user_id = st.selectbox(
            "Usuário",
            options=user_ids,
            format_func=lambda user_id: emails[user_id],
            index=None,
            placeholder="Escolha o usuário",
        )
        submitted = st.form_submit_button(
            "Gerar senha temporária",
            type="primary",
        )

    if submitted:
        if target_user_id is None:
            st.warning("Escolha o usuário.")
        else:
            temporary_password = generate_temporary_password()

            try:
                supabase_admin.set_temporary_password(
                    requester=client,
                    target_user_id=target_user_id,
                    temporary_password=temporary_password,
                )
            except supabase_admin.NotAuthorizedError:
                st.error("Acesso restrito ao TI.")
                return
            except Exception as error:
                print(
                    f"[TI] Falha ao redefinir senha: {type(error).__name__}",
                    flush=True,
                )
                st.error("Não foi possível redefinir a senha. Tente novamente.")
                return

            try:
                reset = log_password_reset(
                    client=client,
                    target_user_id=target_user_id,
                )
                require_password_change(
                    client=client,
                    user_id=target_user_id,
                    reset_id=reset.get("id"),
                )
            except Exception as error:
                print(
                    f"[TI] Falha ao registrar reset: {type(error).__name__}",
                    flush=True,
                )
                st.session_state[TEMPORARY_PASSWORD_STATE_KEY] = (
                    emails[target_user_id],
                    temporary_password,
                )
                st.warning(
                    "A senha foi trocada, mas não foi possível registrar o "
                    "reset nem obrigar a troca no próximo acesso. Peça para a "
                    "pessoa trocar a senha assim que entrar."
                )
                return

            st.session_state[TEMPORARY_PASSWORD_STATE_KEY] = (
                emails[target_user_id],
                temporary_password,
            )
            st.rerun()

    st.subheader("Histórico de resets")

    try:
        resets = list_password_resets(client=client)
    except Exception as error:
        print(f"[TI] Falha ao listar resets: {type(error).__name__}", flush=True)
        st.error("Não foi possível carregar o histórico.")
        return

    if not resets:
        st.caption("Nenhum reset feito ainda.")
        return

    st.dataframe(
        [
            {
                "Quando": format_date(reset["created_at"]),
                "Usuário": emails.get(str(reset["target_user_id"]), "—"),
                "Feito por": emails.get(str(reset["reset_by"]), "—"),
            }
            for reset in resets
        ],
        hide_index=True,
        use_container_width=True,
    )


def needs_password_change(
    client: Client,
    user_id: str,
) -> bool:
    """Confere uma vez por sessão se o TI exigiu a troca de senha."""
    if not st.session_state.get(PASSWORD_CHECK_STATE_KEY):
        try:
            required = must_change_password(
                client=client,
                user_id=user_id,
            )
        except Exception as error:
            print(
                f"[SENHA] Falha ao conferir troca obrigatória: "
                f"{type(error).__name__}",
                flush=True,
            )
            # Sem conseguir conferir, não libera nem bloqueia para sempre:
            # tenta de novo na próxima atualização da tela.
            return False

        st.session_state[PASSWORD_CHECK_STATE_KEY] = (
            "obrigatoria" if required else "ok"
        )

    return st.session_state[PASSWORD_CHECK_STATE_KEY] == "obrigatoria"


def show_password_change_screen(client: Client) -> None:
    """Única tela disponível enquanto a pessoa não cria a senha nova."""
    st.title("Crie sua nova senha")
    st.write(
        "Sua senha foi redefinida pelo TI. Para continuar, crie uma senha "
        "nova, que só você vai saber."
    )

    with st.form("forced_password_change_form", border=True):
        new_password = st.text_input(
            "Nova senha",
            type="password",
            help=f"Pelo menos {MIN_PASSWORD_LENGTH} caracteres.",
        )
        confirmation = st.text_input(
            "Repita a nova senha",
            type="password",
        )
        submitted = st.form_submit_button(
            "Salvar e continuar",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    if len(new_password) < MIN_PASSWORD_LENGTH:
        st.warning(f"A senha precisa ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
        return

    if new_password != confirmation:
        st.warning("As duas senhas estão diferentes.")
        return

    try:
        client.auth.update_user({"password": new_password})
    except Exception as error:
        print(f"[SENHA] Falha ao trocar senha: {type(error).__name__}", flush=True)
        st.error(
            "Não foi possível salvar a senha. Ela precisa ser diferente da "
            "senha temporária. Tente outra."
        )
        return

    try:
        supabase_admin.clear_own_password_requirement(client)
    except Exception as error:
        print(
            f"[SENHA] Falha ao liberar acesso: {type(error).__name__}",
            flush=True,
        )
        st.error(
            "Sua senha nova foi salva, mas não foi possível liberar o acesso "
            "agora. Saia e entre de novo com a senha nova."
        )
        return

    st.session_state[PASSWORD_CHECK_STATE_KEY] = "ok"
    st.rerun()
