import base64
from html import escape
from io import BytesIO
from pathlib import Path
import re
import traceback

import streamlit as st
from PIL import Image

from database.auth import sign_in
from database.conversations import (
    create_conversation,
    delete_conversation,
    list_conversations,
    rename_conversation,
    set_conversation_pinned,
)
from database.messages import list_messages
from services.chat_service import process_message_stream


PROJECT_ROOT = Path(__file__).resolve().parent
ASSETS_DIR = PROJECT_ROOT / "assets"
CSS_FILE = ASSETS_DIR / "styles.css"
LOGO_FILE = ASSETS_DIR / "logo-latitudes.png"
LOGIN_BACKGROUND_FILE = ASSETS_DIR / "login-background.jpg"


with Image.open(LOGO_FILE) as logo:
    flower = logo.convert("RGBA").crop(
        (
            170,
            0,
            296,
            80,
        )
    )

    page_icon = Image.new(
        "RGBA",
        (128, 128),
        (0, 0, 0, 0),
    )

    icon_position = (
        (page_icon.width - flower.width) // 2,
        (page_icon.height - flower.height) // 2,
    )

    page_icon.alpha_composite(
        flower,
        icon_position,
    )


st.set_page_config(
    page_title="Latitudes AI",
    page_icon=page_icon,
    layout="wide",
    initial_sidebar_state="expanded",
)


def load_css() -> None:
    css = CSS_FILE.read_text(encoding="utf-8")
    st.html(f"<style>{css}</style>")


def file_to_data_uri(
    file_path: Path,
    mime_type: str,
) -> str:
    encoded_file = base64.b64encode(
        file_path.read_bytes()
    ).decode("utf-8")

    return f"data:{mime_type};base64,{encoded_file}"


def get_flower_data_uri() -> str:
    with Image.open(LOGO_FILE) as logo:
        flower_image = logo.convert("RGBA").crop(
            (
                170,
                0,
                296,
                80,
            )
        )

    image_buffer = BytesIO()
    flower_image.save(
        image_buffer,
        format="PNG",
    )

    encoded_image = base64.b64encode(
        image_buffer.getvalue()
    ).decode("utf-8")

    return f"data:image/png;base64,{encoded_image}"


def initialize_session_state() -> None:
    default_values = {
        "authenticated": False,
        "client": None,
        "user_id": None,
        "user_email": None,
        "auth_view": "login",
        "recovery_email": "",
        "selected_conversation_id": None,
        "new_conversation_mode": False,
        "conversation_to_delete": None,
        "conversation_to_rename": None,
        "scroll_to_bottom": False,
    }

    for key, value in default_values.items():
        if key not in st.session_state:
            st.session_state[key] = value


def show_login() -> None:
    st.html(
        """
        <style>
        [data-testid="stSidebar"] {
            display: none;
        }

        [data-testid="stSidebarCollapsedControl"] {
            display: none;
        }

        input[type="password"]::-ms-reveal,
        input[type="password"]::-ms-clear {
            display: none !important;
        }

        input[type="password"]::-webkit-credentials-auto-fill-button {
            display: none !important;
            pointer-events: none !important;
            visibility: hidden !important;
        }
        </style>
        """,
    )

    background_uri = file_to_data_uri(
        LOGIN_BACKGROUND_FILE,
        "image/jpeg",
    )

    flower_uri = get_flower_data_uri()

    visual_column, form_column = st.columns(
        [0.78, 1.22],
        gap="small",
        vertical_alignment="top",
    )

    with visual_column:
        st.html(
            f"""
            <div
                class="login-visual"
                style="background-image: url('{background_uri}');"
            >
                <div class="login-brand">
                    <img
                        class="login-brand-flower"
                        src="{flower_uri}"
                        alt=""
                    >
                    <div class="login-brand-name">
                        Latitudes
                    </div>
                    <span class="login-brand-signature">
                        Viagens de Conhecimento
                    </span>
                </div>
            </div>
            """,
        )

    with form_column:
        st.html(
            """
            <div class="login-heading">
                <h1>Bem-vinda ao<br>Latitudes AI</h1>
                <p>
                    Entre com sua conta corporativa para continuar.
                </p>
            </div>
            """,
        )

        with st.form("login_form"):
            email = st.text_input(
                "E-mail",
                placeholder="nome@latitudes.com.br",
            )

            password = st.text_input(
                "Senha",
                type="password",
            )

            submitted = st.form_submit_button(
                "Entrar",
                use_container_width=True,
            )

        st.button(
            "Esqueci minha senha",
            type="tertiary",
            use_container_width=True,
            disabled=True,
            help=(
                "Disponível após a configuração "
                "do envio de e-mails."
            ),
        )

        if submitted:
            if not email.strip() or not password:
                st.warning(
                    "Preencha o e-mail e a senha."
                )
            else:
                try:
                    client, user = sign_in(
                        email=email,
                        password=password,
                    )
                except Exception:
                    st.error(
                        "E-mail ou senha inválidos."
                    )
                else:
                    st.session_state.authenticated = True
                    st.session_state.client = client
                    st.session_state.user_id = str(user.id)
                    st.session_state.user_email = user.email

                    st.rerun()

        st.html(
            """
            <div class="login-footer">
                Uso exclusivo para colaboradores da Latitudes.
            </div>
            """,
        )


def logout() -> None:
    client = st.session_state.client

    if client is not None:
        try:
            client.auth.sign_out()
        except Exception:
            pass

    st.session_state.clear()
    st.rerun()


def clear_conversation_deletion() -> None:
    st.session_state.conversation_to_delete = None


@st.dialog(
    "Excluir conversa",
    icon=":material/delete:",
    on_dismiss=clear_conversation_deletion,
)
def show_delete_conversation_dialog(
    client,
    conversation: dict,
) -> None:
    st.write(
        "Tem certeza de que deseja excluir "
        f"**{conversation['title']}**?"
    )
    st.caption(
        "A conversa e todas as mensagens dela "
        "serão removidas permanentemente."
    )

    confirm_column, cancel_column = st.columns(2)

    with confirm_column:
        if st.button(
            "Excluir",
            type="primary",
            use_container_width=True,
        ):
            delete_conversation(
                client=client,
                conversation_id=conversation["id"],
            )

            if (
                st.session_state.selected_conversation_id
                == conversation["id"]
            ):
                st.session_state.selected_conversation_id = None

            st.session_state.conversation_to_delete = None
            st.session_state.new_conversation_mode = False
            st.rerun()

    with cancel_column:
        if st.button(
            "Cancelar",
            use_container_width=True,
        ):
            st.session_state.conversation_to_delete = None
            st.rerun()


def clear_conversation_rename() -> None:
    st.session_state.conversation_to_rename = None


@st.dialog(
    "Renomear conversa",
    icon=":material/edit:",
    on_dismiss=clear_conversation_rename,
)
def show_rename_conversation_dialog(
    client,
    conversation: dict,
) -> None:
    current_title = str(conversation["title"])

    with st.form(
        f"rename_conversation_form_{conversation['id']}"
    ):
        new_title = st.text_input(
            "Novo título",
            value=current_title,
            max_chars=80,
        )

        save_column, cancel_column = st.columns(2)

        with save_column:
            save_submitted = st.form_submit_button(
                "Salvar",
                type="primary",
                use_container_width=True,
            )

        with cancel_column:
            cancel_submitted = st.form_submit_button(
                "Cancelar",
                use_container_width=True,
            )

    if cancel_submitted:
        st.session_state.conversation_to_rename = None
        st.rerun()

    if not save_submitted:
        return

    try:
        rename_conversation(
            client=client,
            conversation_id=conversation["id"],
            title=new_title,
        )
    except ValueError as error:
        st.warning(str(error))
    except Exception:
        st.error(
            "Não foi possível renomear a conversa."
        )
    else:
        st.session_state.conversation_to_rename = None
        st.rerun()

def create_title_from_message(content: str) -> str:
    clean_content = " ".join(content.split()).strip()

    if not clean_content:
        return "Nova conversa"

    location_word = (
        r"[A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇ]"
        r"[\wÀ-ÿ'-]*"
    )
    location_pattern = (
        rf"({location_word}"
        rf"(?:\s+(?:(?:de|da|do|das|dos|e)\s+)?"
        rf"{location_word}){{0,2}})"
    )

    entry_match = re.search(
        rf"\bentrada\s+(?:na|no|em)\s+{location_pattern}",
        clean_content,
    )

    if (
        entry_match is not None
        and "requisit" in clean_content.lower()
    ):
        return (
            "Requisitos de entrada "
            f"na {entry_match.group(1)}"
        )

    destination_match = re.search(
        rf"\b(?:para|por|em|na|no)\s+{location_pattern}",
        clean_content,
    )

    if destination_match is not None:
        destination = destination_match.group(1)
        lower_content = clean_content.lower()

        if "roteiro" in lower_content:
            return f"Roteiro de {destination}"

        if "visto" in lower_content:
            return f"Visto para {destination}"

        if "hotel" in lower_content or "hospedagem" in lower_content:
            return f"Hospedagem em {destination}"

        if "restaurante" in lower_content or "gastronomia" in lower_content:
            return f"Gastronomia em {destination}"

    summarized_title = re.sub(
        (
            r"^(?:por favor,?\s*)?"
            r"(?:pesquise(?:\s+na internet)?|"
            r"explique|informe|resuma|mostre|"
            r"quero saber|gostaria de saber|"
            r"crie|faça)\s+"
        ),
        "",
        clean_content,
        flags=re.IGNORECASE,
    )

    summarized_title = re.sub(
        r"^(?:quais?\s+(?:são|é)|o que é|como funciona)\s+",
        "",
        summarized_title,
        flags=re.IGNORECASE,
    )

    summarized_title = summarized_title.rstrip(" .?!")

    if summarized_title:
        summarized_title = (
            summarized_title[0].upper()
            + summarized_title[1:]
        )
    else:
        summarized_title = "Nova conversa"

    maximum_length = 48

    if len(summarized_title) <= maximum_length:
        return summarized_title

    shortened_title = summarized_title[
        :maximum_length
    ].rsplit(" ", 1)[0]

    return f"{shortened_title}…"


def show_empty_conversation(flower_uri: str) -> None:
    st.html(
        f"""
        <div style="
            min-height: 360px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding: 48px 24px;
        ">
            <img
                src="{flower_uri}"
                alt=""
                style="
                    width: 74px;
                    height: auto;
                    margin-bottom: 22px;
                "
            >
            <h2 style="
                margin: 0 0 12px;
                font-family: Georgia, 'Times New Roman', serif;
                font-size: 32px;
                font-weight: 400;
                color: #333333;
            ">
                Inicie uma nova conversa
            </h2>
            <p style="
                max-width: 520px;
                margin: 0;
                color: #9B9B9C;
                font-size: 16px;
                line-height: 1.6;
            ">
                Envie uma mensagem para pesquisar, organizar
                informações ou criar uma sugestão de roteiro.
            </p>
        </div>
        """,
    )


def display_user_message(content: str) -> None:
    with st.chat_message(
        "user",
        avatar="👤",
    ):
        st.markdown(content)


def stream_assistant_response(
    client,
    user_id: str,
    conversation_id: str,
    prompt: str,
) -> bool:
    with st.chat_message(
        "assistant",
        avatar=page_icon,
    ):
        status_placeholder = st.empty()
        status_placeholder.html(
            """
            <style>
            @keyframes latitudes-cursor-blink {
                0%, 49% { opacity: 1; }
                50%, 100% { opacity: 0; }
            }

            .latitudes-streaming-status {
                color: #9B9B9C;
                font-size: 15px;
            }

            .latitudes-streaming-cursor {
                display: inline-block;
                margin-left: 3px;
                color: #F6862F;
                animation: latitudes-cursor-blink 0.9s infinite;
            }
            </style>

            <div class="latitudes-streaming-status">
                Preparando a resposta
                <span class="latitudes-streaming-cursor">▌</span>
            </div>
            """,
        )

        async def visible_response_stream():
            first_chunk = True

            async for chunk in process_message_stream(
                client=client,
                user_id=user_id,
                conversation_id=conversation_id,
                content=prompt,
            ):
                if first_chunk:
                    status_placeholder.empty()
                    first_chunk = False

                yield chunk

        try:
            st.write_stream(
                visible_response_stream(),
                cursor="▌",
            )
        except Exception:
            traceback.print_exc()
            st.error(
                "Não foi possível processar a mensagem. "
                "Tente novamente."
            )
            return False

    return True


def show_authenticated_area() -> None:
    client = st.session_state.client
    user_id = st.session_state.user_id
    flower_uri = get_flower_data_uri()

    st.html(
        """
        <style>
        [data-testid="stSidebar"]
        [data-testid="stHorizontalBlock"]
        [data-testid="stColumn"]:first-child button {
            justify-content: flex-start !important;
            text-align: left !important;
        }

        [data-testid="stSidebar"]
        [data-testid="stHorizontalBlock"]
        [data-testid="stColumn"]:first-child button p {
            width: 100%;
            overflow: hidden;
            text-align: left !important;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        [data-testid="stSidebar"]
        [data-testid="stHorizontalBlock"]
        [data-testid="stColumn"]:last-child button svg,
        [data-testid="stSidebar"]
        [data-testid="stHorizontalBlock"]
        [data-testid="stColumn"]:last-child button
        [data-testid="stIconMaterial"],
        [data-testid="stSidebar"]
        [data-testid="stHorizontalBlock"]
        [data-testid="stColumn"]:last-child button::after {
            display: none !important;
            content: none !important;
        }

        [data-testid="stSidebar"]
        [data-testid="stHorizontalBlock"]
        [data-testid="stColumn"]:nth-child(2) button {
            min-height: 40px !important;
            padding: 0 !important;
            border: none !important;
            background: transparent !important;
            color: #FFFFFF !important;
            opacity: 0.78 !important;
        }

        [data-testid="stSidebar"]
        [data-testid="stHorizontalBlock"]
        [data-testid="stColumn"]:nth-child(2) button
        [data-testid="stIconMaterial"] {
            color: #FFFFFF !important;
            font-size: 17px !important;
        }

        .scroll-to-bottom-button {
            position: fixed;
            right: 34px;
            bottom: 102px;
            z-index: 999;
            width: 42px;
            height: 42px;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 1px solid rgba(148, 130, 93, 0.28);
            border-radius: 50%;
            background: #FFFFFF;
            box-shadow: 0 5px 18px rgba(98, 82, 52, 0.16);
            color: #625234 !important;
            font-size: 23px;
            line-height: 1;
            text-decoration: none !important;
            opacity: 0;
            pointer-events: none;
            transform: translateY(8px);
            transition:
                opacity 0.2s ease,
                transform 0.2s ease,
                background 0.2s ease;
        }

        .scroll-to-bottom-button.is-visible {
            opacity: 1;
            pointer-events: auto;
            transform: translateY(0);
        }

        .scroll-to-bottom-button:hover {
            border-color: #94825D;
            background: #F2EFE6;
            color: #625234 !important;
        }

        .pending-message-notice {
            margin: 18px 0 10px;
            padding: 16px 18px;
            border: 1px solid rgba(148, 130, 93, 0.25);
            border-radius: 12px;
            background: #F2EFE6;
            color: #625234;
            font-size: 15px;
            line-height: 1.5;
        }

        .pending-message-notice strong {
            display: block;
            margin-bottom: 4px;
            color: #333333;
        }
        </style>
        """,
    )

    try:
        conversations = list_conversations(
            client=client,
            user_id=user_id,
        )
    except Exception:
        st.error(
            "Não foi possível carregar suas conversas."
        )
        return

    conversation_ids = {
        conversation["id"]
        for conversation in conversations
    }

    if st.session_state.new_conversation_mode:
        st.session_state.selected_conversation_id = None
    elif (
        st.session_state.selected_conversation_id
        not in conversation_ids
    ):
        if conversations:
            st.session_state.selected_conversation_id = (
                conversations[0]["id"]
            )
            st.session_state.scroll_to_bottom = True
        else:
            st.session_state.selected_conversation_id = None
            st.session_state.new_conversation_mode = True

    with st.sidebar:
        st.html(
            f"""
            <div class="sidebar-brand">
                <img
                    class="sidebar-flower"
                    src="{flower_uri}"
                    alt=""
                >
                <div>
                    <span class="sidebar-name">
                        Latitudes
                    </span>
                    <span class="sidebar-ai">
                        AI
                    </span>
                </div>
            </div>
            """,
        )


        if st.button(
            "＋ Nova conversa",
            use_container_width=True,
            type="primary",
        ):
            st.session_state.selected_conversation_id = None
            st.session_state.new_conversation_mode = True
            st.rerun()

        st.caption("CONVERSAS")

        if not conversations:
            st.caption(
                "Nenhuma conversa criada."
            )

        for conversation in conversations:
            conversation_id = conversation["id"]
            is_selected = (
                conversation_id
                == st.session_state.selected_conversation_id
            )

            conversation_column, pin_column, menu_column = st.columns(
                [0.76, 0.10, 0.14],
                gap="small",
            )

            with conversation_column:
                if st.button(
                    conversation["title"],
                    key=f"conversation_{conversation_id}",
                    use_container_width=True,
                    type=(
                        "primary"
                        if is_selected
                        else "secondary"
                    ),
                ):
                    st.session_state.selected_conversation_id = (
                        conversation_id
                    )
                    st.session_state.new_conversation_mode = False
                    st.session_state.conversation_to_delete = None
                    st.session_state.scroll_to_bottom = True
                    st.rerun()

            with pin_column:
                if conversation.get("is_pinned"):
                    st.button(
                        "",
                        icon=":material/keep:",
                        key=f"pinned_indicator_{conversation_id}",
                        help="Conversa fixada",
                        type="tertiary",
                        disabled=True,
                        use_container_width=True,
                    )

            with menu_column:
                with st.popover(
                    "⋮",
                    key=f"conversation_menu_{conversation_id}",
                    help="Opções da conversa",
                    type="tertiary",
                    use_container_width=True,
                ):
                    is_pinned = bool(
                        conversation.get("is_pinned")
                    )

                    pin_label = (
                        "Desafixar conversa"
                        if is_pinned
                        else "Fixar conversa"
                    )

                    pin_icon = (
                        ":material/keep_off:"
                        if is_pinned
                        else ":material/keep:"
                    )

                    if st.button(
                        pin_label,
                        icon=pin_icon,
                        key=f"pin_conversation_{conversation_id}",
                        type="tertiary",
                        use_container_width=True,
                    ):
                        set_conversation_pinned(
                            client=client,
                            conversation_id=conversation_id,
                            is_pinned=not is_pinned,
                        )
                        st.rerun()

                    if st.button(
                        "Renomear conversa",
                        icon=":material/edit:",
                        key=f"rename_conversation_{conversation_id}",
                        type="tertiary",
                        use_container_width=True,
                    ):
                        st.session_state.conversation_to_rename = (
                            conversation_id
                        )
                        st.session_state.conversation_to_delete = None
                        st.rerun()

                    if st.button(
                        "Excluir conversa",
                        icon=":material/delete:",
                        key=f"delete_conversation_{conversation_id}",
                        type="tertiary",
                        use_container_width=True,
                    ):
                        st.session_state.conversation_to_delete = (
                            conversation_id
                        )
                        st.rerun()

        st.divider()

        st.caption("Usuário conectado")
        st.write(st.session_state.user_email)

        if st.button(
            "Sair",
            use_container_width=True,
        ):
            logout()

    conversation_to_delete = (
        st.session_state.conversation_to_delete
    )

    if conversation_to_delete is not None:
        target_conversation = next(
            (
                conversation
                for conversation in conversations
                if conversation["id"] == conversation_to_delete
            ),
            None,
        )

        if target_conversation is not None:
            show_delete_conversation_dialog(
                client=client,
                conversation=target_conversation,
            )

    conversation_to_rename = (
        st.session_state.conversation_to_rename
    )

    if conversation_to_rename is not None:
        target_conversation = next(
            (
                conversation
                for conversation in conversations
                if conversation["id"] == conversation_to_rename
            ),
            None,
        )

        if target_conversation is not None:
            show_rename_conversation_dialog(
                client=client,
                conversation=target_conversation,
            )

    selected_id = st.session_state.selected_conversation_id

    if selected_id is None:
        st.html(
            """
            <div class="chat-header">
                <h1>Nova conversa</h1>
                <p>
                    Converse com a assistente corporativa da Latitudes.
                </p>
            </div>
            """,
        )

        show_empty_conversation(flower_uri)

        prompt = st.chat_input(
            "Digite sua mensagem...",
        )

        if prompt:
            conversation_title = create_title_from_message(
                prompt
            )

            try:
                new_conversation = create_conversation(
                    client=client,
                    user_id=user_id,
                    title=conversation_title,
                )
            except Exception:
                st.error(
                    "Não foi possível criar uma conversa."
                )
            else:
                new_conversation_id = new_conversation["id"]
                st.session_state.selected_conversation_id = (
                    new_conversation_id
                )
                st.session_state.new_conversation_mode = False
                st.session_state.scroll_to_bottom = True

                display_user_message(prompt)

                response_saved = stream_assistant_response(
                    client=client,
                    user_id=user_id,
                    conversation_id=new_conversation_id,
                    prompt=prompt,
                )

                if response_saved:
                    st.rerun()

        return

    selected_conversation = next(
        conversation
        for conversation in conversations
        if conversation["id"] == selected_id
    )

    conversation_title = escape(
        str(selected_conversation["title"])
    )

    st.html(
        f"""
        <div class="chat-header">
            <h1>{conversation_title}</h1>
            <p>
                Converse com a assistente corporativa da Latitudes.
            </p>
        </div>
        """,
    )

    try:
        messages = list_messages(
            client=client,
            conversation_id=selected_id,
        )
    except Exception:
        st.error(
            "Não foi possível carregar o histórico."
        )
        return

    if not messages:
        show_empty_conversation(flower_uri)

    for message in messages:
        role = message.get("role")
        content = message.get("content", "")

        if role == "user":
            with st.chat_message(
                "user",
                avatar="👤",
            ):
                st.markdown(content)

        elif role == "assistant":
            with st.chat_message(
                "assistant",
                avatar=page_icon,
            ):
                st.markdown(content)

    if messages:
        st.html(
            """
            <div id="latitudes-chat-bottom"></div>
            <a
                class="scroll-to-bottom-button"
                href="#latitudes-chat-bottom"
                title="Ir para a mensagem mais recente"
                aria-label="Ir para a mensagem mais recente"
            >
                ↓
            </a>
            """,
        )

        should_auto_scroll = (
            "true"
            if st.session_state.scroll_to_bottom
            else "false"
        )

        st.html(
            f"""
                <script>
                setTimeout(() => {{
                    const parentWindow = window.parent;
                    const parentDocument = parentWindow.document;
                    const target = parentDocument
                        .getElementById("latitudes-chat-bottom");
                    const button = parentDocument
                        .querySelector(".scroll-to-bottom-button");

                    if (!target || !button) {{
                        return;
                    }}

                    if (
                        parentWindow.__latitudesScrollHandler
                    ) {{
                        parentDocument.removeEventListener(
                            "scroll",
                            parentWindow.__latitudesScrollHandler,
                            true
                        );

                        parentWindow.removeEventListener(
                            "resize",
                            parentWindow.__latitudesScrollHandler
                        );
                    }}

                    const updateButtonVisibility = () => {{
                        const targetPosition =
                            target.getBoundingClientRect().top;
                        const viewportHeight =
                            parentWindow.innerHeight ||
                            parentDocument.documentElement.clientHeight;

                        button.classList.toggle(
                            "is-visible",
                            targetPosition > viewportHeight - 70
                        );
                    }};

                    parentWindow.__latitudesScrollHandler =
                        updateButtonVisibility;

                    parentDocument.addEventListener(
                        "scroll",
                        updateButtonVisibility,
                        {{ passive: true, capture: true }}
                    );

                    parentWindow.addEventListener(
                        "resize",
                        updateButtonVisibility,
                        {{ passive: true }}
                    );

                    if ({should_auto_scroll}) {{
                        target.scrollIntoView({{
                            behavior: "instant",
                            block: "end"
                        }});
                    }}

                    setTimeout(updateButtonVisibility, 100);
                }}, 180);
                </script>
                """,
            unsafe_allow_javascript=True,
        )

        if st.session_state.scroll_to_bottom:
            st.session_state.scroll_to_bottom = False

    pending_message = (
        messages[-1]
        if messages and messages[-1].get("role") == "user"
        else None
    )

    if pending_message is not None:
        st.html(
            """
            <div class="pending-message-notice">
                <strong>Mensagem aguardando resposta</strong>
                A conexão foi interrompida antes de a assistente
                concluir a última solicitação. Tente novamente
                para continuar esta conversa.
            </div>
            """,
        )

        if st.button(
            "Tentar novamente",
            icon=":material/refresh:",
            type="primary",
        ):
            response_saved = stream_assistant_response(
                client=client,
                user_id=user_id,
                conversation_id=selected_id,
                prompt=pending_message["content"],
            )

            if response_saved:
                st.session_state.scroll_to_bottom = True
                st.rerun()

        st.chat_input(
            "Conclua a mensagem pendente para continuar...",
            disabled=True,
        )

        return

    prompt = st.chat_input(
        "Digite sua mensagem...",
    )

    if prompt:
        display_user_message(prompt)

        stream_assistant_response(
            client=client,
            user_id=user_id,
            conversation_id=selected_id,
            prompt=prompt,
        )


load_css()
initialize_session_state()

if not st.session_state.authenticated:
    show_login()
    st.stop()

show_authenticated_area()
