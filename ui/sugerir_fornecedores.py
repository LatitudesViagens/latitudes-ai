"""Botão "Sugerir fornecedores para a base", na resposta da conversa.

Quando a pessoa anexa uma planilha ou documento, a ÁGORA organiza os
fornecedores em fichas (services/fornecedores.py), a pessoa revisa numa
tabela e envia. Tudo entra como PENDENTE: o TI aprova no Painel de controle.
A ficha leva só nome, cidade, país, estrelas, serviço e contato geral.
"""

import re
import traceback

import pandas
import streamlit as st
from supabase import Client

from database.ai_usage import log_ai_usage
from database.attachments import TEXT_ATTACHMENT_MIME_TYPES, extract_attachment_text
from database.suppliers import (
    STATUS_PENDING,
    has_suggestions_for_message,
    list_suppliers,
    suggest_suppliers,
)
from services.custos import collect_usage

COLUMNS = {
    "include": "Incluir",
    "name": "Nome",
    "city": "Cidade",
    "country": "País",
    "stars": "Estrelas",
    "service": "Serviço",
    "contact": "Contato geral",
}


def supplier_columns_config() -> dict:
    return {
        "include": st.column_config.CheckboxColumn(COLUMNS["include"], width="small"),
        "name": st.column_config.TextColumn(COLUMNS["name"], required=True, max_chars=200),
        "city": st.column_config.TextColumn(COLUMNS["city"], max_chars=120),
        "country": st.column_config.TextColumn(COLUMNS["country"], required=True, max_chars=80),
        "stars": st.column_config.NumberColumn(
            COLUMNS["stars"],
            min_value=1,
            max_value=5,
            step=1,
            format="%d ★",
            width="small",
        ),
        "service": st.column_config.TextColumn(COLUMNS["service"], required=True, max_chars=300),
        "contact": st.column_config.TextColumn(COLUMNS["contact"], max_chars=300),
    }


# "Quero sugerir este fornecedor para a base", "cadastrar um hotel"...: a
# pessoa pode sugerir sem arquivo, preenchendo a ficha na mesma janela.
SUGGESTION_REQUEST = re.compile(
    r"\b(?:sugerir|sugiro|sugestao|indicar|indico|cadastrar|cadastro|"
    r"adicionar|adiciona|incluir|inclua|colocar|registrar)\b"
    r".{0,80}\b(?:fornecedor\w*|hote(?:l|is)|receptivo\w*|guias?|dmc|"
    r"transportadora\w*|prestador\w*)\b",
    flags=re.IGNORECASE | re.DOTALL,
)
# A resposta da ÁGORA aponta para o botão (instrução do agente); se ela
# disser isso, o botão aparece mesmo que a frase da pessoa fuja do padrão.
BUTTON_LABEL = "Sugerir fornecedor para a base"

# Para quem usa, quem aprova não aparece (pedido da Isabelle, 20/10/2026):
# o texto é genérico. Quem aprova é o TI, no Painel de controle.
SUGGESTION_REVIEW_NOTE = (
    "Após o envio, os fornecedores serão analisados e, depois de aprovados, "
    "passam a ser usados nas operações."
)


def asked_to_suggest(question: str, answer: str = "") -> bool:
    from services.fornecedores import normalize

    return (
        SUGGESTION_REQUEST.search(normalize(question)) is not None
        or BUTTON_LABEL.lower() in (answer or "").lower()
    )


TEXT_COLUMNS = ("name", "city", "country", "service", "contact")


def supplier_frame(
    records: list[dict],
    columns: list[str],
) -> pandas.DataFrame:
    """Tabela com o tipo certo em cada coluna. Coluna toda vazia (ficha em
    branco, ninguém com contato) vira número (NaN) no pandas, e a tabela de
    edição não deixa digitar texto nela (StreamlitAPIException)."""
    table = pandas.DataFrame(records, columns=columns)

    for name in columns:
        if name in TEXT_COLUMNS or name in {"already", "author", "source", "id"}:
            table[name] = [
                "" if value is None or (isinstance(value, float) and value != value) else str(value)
                for value in table[name]
            ]
            table[name] = table[name].astype("object")
        elif name == "stars":
            table[name] = pandas.to_numeric(table[name], errors="coerce").astype("float64")
        elif name == "include":
            table[name] = table[name].fillna(False).astype(bool)

    return table


def file_attachments(message: dict | None) -> list[dict]:
    """Anexos com texto (planilha, Word, CSV, texto, PDF) de uma mensagem."""
    return [
        attachment
        for attachment in (message or {}).get("attachments") or []
        if str(attachment.get("mime_type", "")) in TEXT_ATTACHMENT_MIME_TYPES
    ]


def is_included(row: dict) -> bool:
    """Marcada em "Incluir", ou linha nova (do "+", que nasce sem marca)
    com nome preenchido. Só fica de fora o que a pessoa desmarcou."""
    include = row.get("include")

    if include is True:
        return True

    if include is False:
        return False

    name = row.get("name")
    return isinstance(name, str) and bool(name.strip())


def incomplete_rows(rows: list[dict]) -> int:
    return sum(
        1
        for row in rows
        if not str(row.get("name") or "").strip()
        or not str(row.get("country") or "").strip()
        or not str(row.get("service") or "").strip()
    )


def text_for_privacy_check(rows: list[dict]) -> str:
    """Texto para a trava da LGPD. O contato geral (e-mail e telefone da
    empresa) fica de fora: os padrões fixos o bloqueariam sempre."""
    lines = [
        "LISTA DE FORNECEDORES (empresas e prestadores de serviço de viagem, "
        "não clientes):"
    ]

    for row in rows:
        lines.append(
            " | ".join(
                str(row.get(name) or "")
                for name in ("name", "city", "country", "service")
            )
        )

    return "\n".join(lines)


def _state_key(message_id: str) -> str:
    return f"suppliers_suggested_{message_id}"


def _extraction_key(message_id: str) -> str:
    return f"suppliers_extraction_{message_id}"


def _extract(
    client: Client,
    message: dict,
    conversation_id: str | None,
) -> dict:
    from services.fornecedores import extract_suppliers

    suppliers: list[dict] = []
    failed_parts = 0
    truncated = False
    unreadable: list[str] = []

    with collect_usage() as usages:
        if not file_attachments(message):
            # Sugestão escrita na mensagem: a ficha vem do próprio texto (o
            # que faltar, a pessoa preenche na tabela).
            result = extract_suppliers(
                text=str(message.get("content", "")),
                file_name="mensagem da conversa",
            )
            suppliers.extend(result.suppliers)

        for attachment in file_attachments(message):
            name = str(attachment.get("name", "arquivo"))

            try:
                text = extract_attachment_text(
                    client=client,
                    attachment=attachment,
                )
            except Exception as error:
                print(f"[FORNECEDORES] Falha ao ler o anexo: {type(error).__name__}", flush=True)
                unreadable.append(name)
                continue

            if not text.strip():
                unreadable.append(name)
                continue

            result = extract_suppliers(
                text=text,
                file_name=name,
            )
            suppliers.extend(result.suppliers)
            failed_parts += result.failed_parts
            truncated = truncated or result.truncated

    log_ai_usage(
        client=client,
        usages=usages,
        conversation_id=conversation_id,
    )

    return {
        "suppliers": suppliers,
        "failed_parts": failed_parts,
        "truncated": truncated,
        "unreadable": unreadable,
        "files": ", ".join(str(item.get("name", "")) for item in file_attachments(message)) or None,
    }


STAR_OPTIONS = ["Sem classificação", "1", "2", "3", "4", "5"]


def _existing_keys(client: Client) -> dict[tuple[str, str, str], str]:
    """Fornecedores que não podem ser sugeridos de novo, com o motivo: os já
    aprovados e as sugestões que aguardam análise (as da própria pessoa; o
    RLS não mostra as dos outros)."""
    from services.fornecedores import ensure_suppliers_loaded, loaded_suppliers, supplier_key

    existing: dict[tuple[str, str, str], str] = {}

    try:
        for item in list_suppliers(client=client, status=STATUS_PENDING, limit=2000):
            existing[supplier_key(item)] = "já foi sugerido e aguarda análise"
    except Exception as error:
        print(f"[FORNECEDORES] Falha ao ler sugestões pendentes: {type(error).__name__}", flush=True)

    ensure_suppliers_loaded(client)

    for item in loaded_suppliers() or []:
        existing[supplier_key(item)] = "já está na base"

    return existing


def _describe(row: dict) -> str:
    place = ", ".join(
        str(row.get(name)).strip()
        for name in ("city", "country")
        if isinstance(row.get(name), str) and row.get(name).strip()
    )
    return f"{str(row.get('name') or '').strip()} ({place})" if place else str(row.get("name") or "").strip()


def _duplicate_messages(
    rows: list[dict],
    existing: dict[tuple[str, str, str], str],
) -> list[str]:
    """Um aviso por fornecedor repetido (na base, aguardando análise ou
    duas vezes no próprio envio). Mesmo critério de sempre: nome, cidade e
    país, sem acentos e sem maiúsculas."""
    from services.fornecedores import supplier_key

    messages = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows:
        key = supplier_key(row)

        if key in existing:
            messages.append(f"{_describe(row)} {existing[key]}.")
        elif key in seen:
            messages.append(f"{_describe(row)} aparece mais de uma vez.")

        seen.add(key)

    return messages


def _send(
    client: Client,
    rows: list[dict],
    message_id: str,
    source_file: str | None,
    conversation_id: str | None,
) -> None:
    """Trava da LGPD e envio (comum ao formulário e à tabela)."""
    from services.personal_data_check import find_client_data

    with (
        collect_usage() as usages,
        st.spinner("Verificando dados de clientes..."),
    ):
        try:
            check = find_client_data(text_for_privacy_check(rows))
        except Exception:
            traceback.print_exc()
            check = None

    log_ai_usage(
        client=client,
        usages=usages,
        conversation_id=conversation_id,
    )

    if check is None or check.found is None:
        st.error("Não foi possível verificar dados de clientes agora. Tente de novo em instantes.")
        return

    if check.found:
        st.warning(
            "A sugestão tem dados de clientes e não pode ir para a base (LGPD): "
            + "; ".join(check.items[:5])
        )
        return

    try:
        sent = suggest_suppliers(
            client=client,
            suppliers=rows,
            source_message_id=message_id,
            source_file=source_file,
        )
    except Exception:
        traceback.print_exc()
        st.error("Não foi possível enviar. Tente novamente.")
        return

    st.session_state[_state_key(message_id)] = True

    for key in [key for key in st.session_state if str(key).startswith(f"suppliers_form_{message_id}")]:
        st.session_state.pop(key, None)

    st.session_state.pop(_extraction_key(message_id), None)
    st.toast(f"{sent} fornecedor(es) enviado(s). {SUGGESTION_REVIEW_NOTE}")
    st.rerun()


def _form_cards_key(message_id: str) -> str:
    return f"suppliers_form_{message_id}_cards"


def _add_card(message_id: str) -> None:
    from uuid import uuid4

    st.session_state[_form_cards_key(message_id)].append({"uid": uuid4().hex[:8]})


def _remove_card(
    message_id: str,
    uid: str,
) -> None:
    cards = st.session_state[_form_cards_key(message_id)]
    st.session_state[_form_cards_key(message_id)] = [card for card in cards if card["uid"] != uid]


def _show_form(
    client: Client,
    message_id: str,
    suggested: list[dict],
    conversation_id: str | None,
) -> None:
    """Sugestão digitada: campos de formulário (um bloco por fornecedor), já
    preenchidos com o que a pessoa escreveu na mensagem."""
    from uuid import uuid4

    cards_key = _form_cards_key(message_id)

    if cards_key not in st.session_state:
        st.session_state[cards_key] = [
            {"uid": uuid4().hex[:8], **supplier}
            for supplier in (suggested or [{}])
        ]

    cards = st.session_state[cards_key]
    rows = []

    for number, card in enumerate(cards, start=1):
        uid = card["uid"]
        prefix = f"suppliers_form_{message_id}_{uid}"

        with st.container(border=True):
            title_column, remove_column = st.columns(
                [6, 1],
                vertical_alignment="center",
            )
            title_column.markdown(f"**Fornecedor {number}**" if len(cards) > 1 else "**Fornecedor**")

            if len(cards) > 1:
                remove_column.button(
                    "",
                    icon=":material/delete:",
                    type="tertiary",
                    key=f"{prefix}_remove",
                    help="Tirar este fornecedor",
                    on_click=_remove_card,
                    args=(message_id, uid),
                )

            name_column, service_column = st.columns(2)
            name = name_column.text_input(
                "Nome *",
                value=card.get("name") or "",
                max_chars=200,
                key=f"{prefix}_name",
            )
            service = service_column.text_input(
                "Serviço *",
                value=card.get("service") or "",
                max_chars=300,
                placeholder="Ex.: Hotel, Receptivo (DMC), Guia em português",
                key=f"{prefix}_service",
            )
            city_column, country_column, stars_column = st.columns([2, 2, 1])
            city = city_column.text_input(
                "Cidade",
                value=card.get("city") or "",
                max_chars=120,
                key=f"{prefix}_city",
            )
            country = country_column.text_input(
                "País *",
                value=card.get("country") or "",
                max_chars=80,
                key=f"{prefix}_country",
            )
            stars_value = str(card.get("stars") or "")
            stars = stars_column.selectbox(
                "Estrelas",
                options=STAR_OPTIONS,
                index=STAR_OPTIONS.index(stars_value) if stars_value in STAR_OPTIONS else 0,
                key=f"{prefix}_stars",
            )
            contact = st.text_input(
                "Contato geral",
                value=card.get("contact") or "",
                max_chars=300,
                placeholder="Site, e-mail ou telefone da empresa",
                key=f"{prefix}_contact",
            )

        rows.append(
            {
                "name": name,
                "service": service,
                "city": city,
                "country": country,
                "stars": stars if stars.isdigit() else None,
                "contact": contact,
            }
        )

    st.button(
        "Adicionar outro fornecedor",
        icon=":material/add:",
        type="tertiary",
        key=f"suppliers_form_{message_id}_add",
        on_click=_add_card,
        args=(message_id,),
    )

    if not st.button(
        "Sugerir para a base",
        icon=":material/send:",
        type="primary",
        key=f"suppliers_form_{message_id}_send",
    ):
        return

    missing = incomplete_rows(rows)

    if missing:
        st.error("Preencha os campos com * (nome, serviço e país) antes de enviar.")
        return

    duplicates = _duplicate_messages(
        rows=rows,
        existing=_existing_keys(client),
    )

    if duplicates:
        # Duplicado não sobe: a pessoa vê o aviso e corrige ou tira o bloco.
        st.warning(
            "Não foi possível enviar: este fornecedor já existe. "
            + " ".join(duplicates)
            if len(rows) == 1
            else "Não foi possível enviar. Fornecedores repetidos: " + " ".join(duplicates)
        )
        return

    _send(
        client=client,
        rows=rows,
        message_id=message_id,
        source_file=None,
        conversation_id=conversation_id,
    )


def _show_table(
    client: Client,
    message_id: str,
    extraction: dict,
    conversation_id: str | None,
) -> None:
    """Fornecedores de um arquivo: tabela (podem ser dezenas)."""
    from services.fornecedores import supplier_key

    suppliers = extraction["suppliers"]

    if not suppliers:
        st.info("Nenhum fornecedor encontrado no arquivo.")
        return

    st.caption("Revise antes de enviar: corrija o que precisar e desmarque o que não deve entrar.")

    # Quem já existe (na base ou aguardando análise) vem desmarcado.
    existing = _existing_keys(client)
    table = supplier_frame(
        records=[
            {
                "include": supplier_key(supplier) not in existing,
                **supplier,
                "already": existing.get(supplier_key(supplier), ""),
            }
            for supplier in suppliers
        ],
        columns=[*COLUMNS, "already"],
    )
    config = supplier_columns_config()
    config["already"] = st.column_config.TextColumn("Já existe?", disabled=True)
    edited = st.data_editor(
        table,
        column_config=config,
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
        key=f"suppliers_editor_{message_id}",
    )
    rows = [row for row in edited.to_dict(orient="records") if is_included(row)]

    if not st.button(
        f"Enviar {len(rows)} para aprovação",
        icon=":material/send:",
        type="primary",
        disabled=not rows,
        key=f"suppliers_send_{message_id}",
    ):
        return

    missing = incomplete_rows(rows)

    if missing:
        st.error(f"Preencha nome, país e serviço em {missing} linha(s) antes de enviar.")
        return

    duplicates = _duplicate_messages(
        rows=rows,
        existing=existing,
    )

    if duplicates:
        # Duplicado não sobe: desmarque ou corrija as linhas avisadas.
        st.warning(
            "Não foi possível enviar. Desmarque ou corrija: " + " ".join(duplicates[:10])
            + (f" (e mais {len(duplicates) - 10})" if len(duplicates) > 10 else "")
        )
        return

    _send(
        client=client,
        rows=rows,
        message_id=message_id,
        source_file=extraction["files"],
        conversation_id=conversation_id,
    )


@st.dialog("Sugerir fornecedores para a base", width="large")
def _suggestion_dialog(
    client: Client,
    message: dict,
    conversation_id: str | None,
) -> None:
    message_id = str(message["id"])
    extraction_key = _extraction_key(message_id)
    from_file = bool(file_attachments(message))

    if extraction_key not in st.session_state:
        with st.spinner(
            "Lendo o arquivo e organizando os fornecedores..."
            if from_file
            else "Preparando a ficha..."
        ):
            st.session_state[extraction_key] = _extract(
                client=client,
                message=message,
                conversation_id=conversation_id,
            )

    extraction = st.session_state[extraction_key]

    for name in extraction["unreadable"]:
        st.warning(f"Não foi possível ler {name}.")

    if extraction["failed_parts"]:
        st.warning("Parte do arquivo não pôde ser organizada. Confira se falta algum fornecedor.")

    if extraction["truncated"]:
        st.warning("O arquivo é grande: só a primeira parte foi lida.")

    st.caption(
        "A ficha leva só nome, cidade, país, estrelas, serviço e contato "
        "geral da empresa (sem preços, propostas ou contatos pessoais). "
        + SUGGESTION_REVIEW_NOTE
    )

    if from_file:
        _show_table(
            client=client,
            message_id=message_id,
            extraction=extraction,
            conversation_id=conversation_id,
        )
    else:
        _show_form(
            client=client,
            message_id=message_id,
            suggested=extraction["suppliers"],
            conversation_id=conversation_id,
        )


def show_supplier_suggestion_button(
    client: Client,
    question_message: dict,
    conversation_id: str | None,
    answer: str = "",
) -> None:
    """Botão abaixo da resposta quando a pergunta trouxe planilha ou
    documento, ou quando a pessoa pediu para sugerir um fornecedor. As
    sugestões ficam ligadas à mensagem da pessoa."""
    has_files = bool(file_attachments(question_message))

    if not has_files and not asked_to_suggest(
        question=str(question_message.get("content", "")),
        answer=answer,
    ):
        return

    message_id = str(question_message["id"])
    state_key = _state_key(message_id)

    if state_key not in st.session_state:
        try:
            st.session_state[state_key] = has_suggestions_for_message(
                client=client,
                source_message_id=message_id,
            )
        except Exception:
            # Sem a tabela (migração 014) ou sem conexão: sem o botão.
            st.session_state[state_key] = None

    if st.session_state[state_key] is None:
        return

    if st.session_state[state_key]:
        st.caption("Fornecedor(es) enviado(s) para a base de conhecimento.")
        return

    if st.button(
        "Sugerir fornecedores do arquivo para a base" if has_files else BUTTON_LABEL,
        icon=":material/storefront:",
        type="tertiary",
        key=f"suppliers_suggest_{message_id}",
        help=(
            "Abre a ficha (nome, cidade, país, estrelas, serviço e contato "
            "geral) para você conferir e enviar. " + SUGGESTION_REVIEW_NOTE
        ),
    ):
        _suggestion_dialog(
            client=client,
            message=question_message,
            conversation_id=conversation_id,
        )
