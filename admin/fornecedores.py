"""Aba Fornecedores do Painel de controle (migração 015).

Quem vê: TI e coordenadores de Operações (para os coordenadores, é a única
aba do Painel). Operadoras não têm tela: leem os fornecedores pela conversa
com a ÁGORA. Os demais não veem nem pela conversa.

- Cadastrar: na mão (formulário) ou importando uma planilha/documento (a IA
  organiza as fichas numa tabela para revisar). Entra já aprovado.
- Cadastrados: filtros, "Selecionar todos"/"Limpar seleção" ou um a um,
  editar e retirar.

Duplicado (mesmo nome, cidade e país, sem acentos) não é cadastrado e dados
de clientes são barrados (LGPD). Toda mudança limpa a lista da memória do
servidor: a ÁGORA usa já na próxima pergunta.
"""

import traceback
from uuid import uuid4

import pandas
import streamlit as st
from supabase import Client

from database.ai_usage import log_ai_usage
from database.attachments import DOCX_MIME_TYPE, MAX_FILE_SIZE, extract_file_text
from database.suppliers import (
    FIELDS,
    MAX_NOTES,
    OPTIONS,
    PRODUCT_TYPES,
    PROFILES,
    STATUS_APPROVED,
    STATUS_REJECTED,
    clean_notes,
    clean_option,
    clean_product_type,
    clean_profile,
    create_suppliers,
    list_suppliers,
    review_supplier,
)
from services.custos import collect_usage
from services.fornecedores import (
    forget_suppliers_cache,
    normalize,
    split_duplicates,
    supplier_key,
)

FLASH_KEY = "admin_suppliers_flash"

COLUMNS = {
    "include": "Incluir",
    "name": "Nome",
    "city": "Cidade",
    "country": "País",
    "option": "Opção",
    "product_type": "Tipo",
    "profile": "Perfil",
    "service": "Serviço",
    "contact": "Contato",
    "notes": "Observação",
}
TEXT_COLUMNS = ("name", "city", "country", "option", "product_type", "profile", "service", "contact", "notes")

NOT_INFORMED = "Não informado"
OPTION_CHOICES = [NOT_INFORMED, *(f"Opção {number}" for number in OPTIONS)]
PRODUCT_TYPE_CHOICES = [NOT_INFORMED, *PRODUCT_TYPES]
PRODUCT_TYPE_HELP = "GRP: grupo. PVT: privativo. FIT: individual."

NO_PROFILE = "Não informado"
PROFILE_OPTIONS = [NO_PROFILE, *PROFILES]
PROFILE_HELP = (
    "VIP: alto padrão, luxo. BUDGET: grupos mais simples, orçamento "
    "enxuto. SERVIÇOS SEPARADOS: serviços vendidos avulsos, fora de pacote."
)
CARD_NOTE = (
    "A ficha leva nome, cidade, país, opção, tipo de produto, perfil, "
    "serviço, contato da empresa e observação (sem contatos pessoais nem "
    "dados de clientes)."
)
# Qualquer planilha (Excel novo e antigo, LibreOffice, CSV/TSV), além de
# Word, PDF e texto.
SPREADSHEET_TYPES = ("xlsx", "xlsm", "xlsb", "xls", "ods")
UPLOAD_TYPES = [*SPREADSHEET_TYPES, "csv", "tsv", "txt", "docx", "pdf"]
MAX_IMPORT_TEXT = 2 * 1024 * 1024


# --------------------------------------------------------------------------
# Tabelas e regras comuns
# --------------------------------------------------------------------------


def supplier_columns_config() -> dict:
    return {
        "include": st.column_config.CheckboxColumn(COLUMNS["include"], width="small"),
        "name": st.column_config.TextColumn(COLUMNS["name"], required=True, max_chars=200),
        "city": st.column_config.TextColumn(COLUMNS["city"], max_chars=120),
        "country": st.column_config.TextColumn(COLUMNS["country"], required=True, max_chars=80),
        "option": st.column_config.SelectboxColumn(
            COLUMNS["option"],
            options=[str(number) for number in OPTIONS],
            help="Opção 1, 2 ou 3 (a 1 é a preferida).",
            width="small",
        ),
        "product_type": st.column_config.SelectboxColumn(
            COLUMNS["product_type"],
            options=list(PRODUCT_TYPES),
            help=PRODUCT_TYPE_HELP,
            width="small",
        ),
        "profile": st.column_config.SelectboxColumn(
            COLUMNS["profile"],
            options=list(PROFILES),
            help=PROFILE_HELP,
        ),
        "service": st.column_config.TextColumn(COLUMNS["service"], required=True, max_chars=300),
        "contact": st.column_config.TextColumn(COLUMNS["contact"], max_chars=300),
        "notes": st.column_config.TextColumn(COLUMNS["notes"], max_chars=MAX_NOTES),
    }


def supplier_frame(
    records: list[dict],
    columns: list[str],
) -> pandas.DataFrame:
    """Tabela com o tipo certo em cada coluna. Coluna toda vazia (ninguém
    com contato) vira número (NaN) no pandas, e a tabela de edição não deixa
    digitar texto nela (StreamlitAPIException)."""
    table = pandas.DataFrame(records, columns=columns)

    for name in columns:
        if name in TEXT_COLUMNS or name in {"already", "id"}:
            table[name] = [
                ""
                if value is None or (isinstance(value, float) and value != value)
                # Opção vem do banco como número (2) ou da IA como 2.0.
                else str(clean_option(value) or "") if name == "option"
                else str(value)
                for value in table[name]
            ]
            table[name] = table[name].astype("object")
        elif name == "include":
            table[name] = table[name].fillna(False).astype(bool)

    return table


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
    """Texto para a trava da LGPD. O contato da empresa (e-mail e telefone)
    fica de fora: os padrões fixos o bloqueariam sempre. A observação, que é
    texto livre, entra."""
    lines = [
        "LISTA DE FORNECEDORES (empresas e prestadores de serviço de viagem, "
        "não clientes):"
    ]

    for row in rows:
        lines.append(
            " | ".join(
                str(row.get(name) or "")
                for name in ("name", "city", "country", "service", "notes")
            )
        )

    return "\n".join(lines)


def _describe(row: dict) -> str:
    place = ", ".join(
        str(row.get(name)).strip()
        for name in ("city", "country")
        if isinstance(row.get(name), str) and row.get(name).strip()
    )
    name = str(row.get("name") or "").strip()
    return f"{name} ({place})" if place else name


def duplicate_messages(
    rows: list[dict],
    existing_keys: set[tuple[str, str, str]],
) -> list[str]:
    """Um aviso por fornecedor repetido (já cadastrado ou duas vezes no
    mesmo envio). Critério: nome, cidade e país, sem acentos e maiúsculas."""
    messages = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows:
        key = supplier_key(row)

        if key in existing_keys:
            messages.append(f"{_describe(row)} já está cadastrado.")
        elif key in seen:
            messages.append(f"{_describe(row)} aparece mais de uma vez.")

        seen.add(key)

    return messages


def _clean_row(row: dict) -> tuple:
    """Valores comparáveis: célula vazia (NaN) = vazio; perfil pelo nome oficial."""
    values = []

    for name in FIELDS:
        value = row.get(name)

        if name == "profile":
            values.append(clean_profile(value))
        elif name == "option":
            values.append(clean_option(value))
        elif name == "product_type":
            values.append(clean_product_type(value))
        elif name == "notes":
            values.append(clean_notes(value) or "")
        elif value is None or (isinstance(value, float) and value != value):
            values.append("")
        else:
            values.append(" ".join(str(value).split()))

    return tuple(values)


def _flash(message: str) -> None:
    st.session_state[FLASH_KEY] = message
    _set_selection(selected=False)
    st.rerun()


def _passes_privacy_check(
    client: Client,
    rows: list[dict],
) -> bool:
    """Trava da LGPD antes de gravar (dados de clientes nunca entram)."""
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
    )

    if check is None or check.found is None:
        st.error("Não foi possível verificar dados de clientes agora. Tente de novo em instantes.")
        return False

    if check.found:
        st.warning(
            "A lista tem dados de clientes e não pode ser cadastrada (LGPD): "
            + "; ".join(check.items[:5])
        )
        return False

    return True


def _save_new(
    client: Client,
    rows: list[dict],
    existing_keys: set[tuple[str, str, str]],
    source_file: str | None,
) -> None:
    """Confere campos, duplicados e LGPD; cadastra já aprovado."""
    missing = incomplete_rows(rows)

    if missing:
        st.error(f"Preencha nome, serviço e país em {missing} fornecedor(es).")
        return

    duplicates = duplicate_messages(
        rows=rows,
        existing_keys=existing_keys,
    )

    if duplicates:
        # Duplicado não entra: aviso e nada é gravado.
        extra = f" (e mais {len(duplicates) - 10})" if len(duplicates) > 10 else ""
        st.warning("Não foi possível cadastrar. " + " ".join(duplicates[:10]) + extra)
        return

    if not _passes_privacy_check(client=client, rows=rows):
        return

    try:
        saved = create_suppliers(
            client=client,
            suppliers=rows,
            reviewer_id=st.session_state.user_id,
            source_file=source_file,
        )
    except Exception:
        traceback.print_exc()
        st.error("Não foi possível cadastrar. Tente novamente.")
        return

    forget_suppliers_cache()

    for key in [key for key in st.session_state if str(key).startswith("suppliers_new_")]:
        st.session_state.pop(key, None)

    # Campo de envio de arquivo novo (o anterior mostraria a planilha já usada).
    st.session_state[UPLOAD_VERSION_KEY] = st.session_state.get(UPLOAD_VERSION_KEY, 0) + 1
    _flash(f"{saved} fornecedor(es) cadastrado(s).")


# --------------------------------------------------------------------------
# Cadastrar na mão
# --------------------------------------------------------------------------

CARDS_KEY = "suppliers_new_cards"


def _add_card() -> None:
    st.session_state[CARDS_KEY].append({"uid": uuid4().hex[:8]})


def _remove_card(uid: str) -> None:
    st.session_state[CARDS_KEY] = [
        card for card in st.session_state[CARDS_KEY] if card["uid"] != uid
    ]


def _show_manual_form(
    client: Client,
    existing_keys: set[tuple[str, str, str]],
) -> None:
    """Campos de formulário, um bloco por fornecedor."""
    cards = st.session_state.setdefault(CARDS_KEY, [{"uid": uuid4().hex[:8]}])
    rows = []

    for number, card in enumerate(cards, start=1):
        prefix = f"suppliers_new_{card['uid']}"

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
                    args=(card["uid"],),
                )

            name_column, service_column = st.columns(2)
            name = name_column.text_input("Nome *", max_chars=200, key=f"{prefix}_name")
            service = service_column.text_input(
                "Serviço *",
                max_chars=300,
                placeholder="Ex.: Hotel, Receptivo (DMC), Guia em português",
                key=f"{prefix}_service",
            )
            city_column, country_column = st.columns(2)
            city = city_column.text_input("Cidade", max_chars=120, key=f"{prefix}_city")
            country = country_column.text_input("País *", max_chars=80, key=f"{prefix}_country")
            option_column, type_column = st.columns(2)
            option = option_column.selectbox(
                "Opção",
                options=OPTION_CHOICES,
                help="Opção 1, 2 ou 3 (a 1 é a preferida).",
                key=f"{prefix}_option",
            )
            product_type = type_column.selectbox(
                "Tipo de produto",
                options=PRODUCT_TYPE_CHOICES,
                help=PRODUCT_TYPE_HELP,
                key=f"{prefix}_product_type",
            )
            profile_column, contact_column = st.columns(2)
            profile = profile_column.selectbox(
                "Perfil",
                options=PROFILE_OPTIONS,
                help=PROFILE_HELP,
                key=f"{prefix}_profile",
            )
            contact = contact_column.text_input(
                "Contato",
                max_chars=300,
                placeholder="Site, e-mail ou telefone da empresa",
                key=f"{prefix}_contact",
            )
            notes = st.text_area(
                "Observação",
                max_chars=MAX_NOTES,
                placeholder="Qualquer anotação sobre o fornecedor",
                height=90,
                key=f"{prefix}_notes",
            )

        rows.append(
            {
                "name": name,
                "service": service,
                "city": city,
                "country": country,
                "option": clean_option(option),
                "product_type": clean_product_type(product_type),
                "profile": clean_profile(profile),
                "contact": contact,
                "notes": clean_notes(notes),
            }
        )

    st.button(
        "Adicionar outro fornecedor",
        icon=":material/add:",
        type="tertiary",
        key="suppliers_new_add",
        on_click=_add_card,
    )

    if st.button(
        "Cadastrar" if len(rows) == 1 else f"Cadastrar {len(rows)} fornecedores",
        icon=":material/save:",
        type="primary",
        key="suppliers_new_save",
    ):
        _save_new(
            client=client,
            rows=rows,
            existing_keys=existing_keys,
            source_file=None,
        )


# --------------------------------------------------------------------------
# Importar planilha
# --------------------------------------------------------------------------

IMPORT_KEY = "suppliers_new_import"
UPLOAD_VERSION_KEY = "suppliers_upload_version"


def _spreadsheet_text(data: bytes) -> str:
    """Todas as abas de uma planilha (xlsx, xlsm, xlsb, xls, ods), uma linha
    por linha da planilha, células separadas por " | "."""
    from io import BytesIO

    from python_calamine import CalamineWorkbook

    workbook = CalamineWorkbook.from_filelike(BytesIO(data))
    parts = []

    for sheet_name in workbook.sheet_names:
        rows = workbook.get_sheet_by_name(sheet_name).to_python(skip_empty_area=True)
        lines = [
            " | ".join("" if value is None else str(value).strip() for value in row)
            for row in rows
            if any(str(value).strip() for value in row if value is not None)
        ]

        if lines:
            parts.append(f"Aba: {sheet_name}\n" + "\n".join(lines))

    return "\n\n".join(parts)


def _plain_text(data: bytes) -> str:
    """CSV, TSV e texto em UTF-8 ou no padrão do Excel no Windows."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    return data.decode("latin-1")


def file_text(
    data: bytes,
    filename: str,
) -> str:
    """Texto do arquivo enviado na aba, pelo tipo (extensão). Erro de
    formato vira ValueError com mensagem para a pessoa."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if not data:
        raise ValueError(f"O arquivo {filename} está vazio.")

    if len(data) > MAX_FILE_SIZE:
        raise ValueError(f"O arquivo {filename} passa de 10 MB.")

    if extension in SPREADSHEET_TYPES:
        try:
            text = _spreadsheet_text(data)
        except Exception as error:
            print(f"[FORNECEDORES] Planilha ilegível: {type(error).__name__}", flush=True)
            raise ValueError(
                f"Não foi possível ler a planilha {filename}. Confira se o "
                "arquivo abre no Excel e não tem senha."
            ) from error
    elif extension in {"csv", "tsv", "txt"}:
        text = _plain_text(data)
    elif extension == "docx":
        text = extract_file_text(data=data, mime_type=DOCX_MIME_TYPE, filename=filename)
    elif extension == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError(f"O PDF {filename} é inválido.")

        text = extract_file_text(data=data, mime_type="application/pdf", filename=filename)
    else:
        raise ValueError(f"O formato de {filename} não é aceito.")

    return text[:MAX_IMPORT_TEXT]


def _read_upload(
    client: Client,
    upload,
) -> dict | None:
    from services.fornecedores import extract_suppliers

    try:
        text = file_text(
            data=upload.getvalue(),
            filename=upload.name,
        )
    except ValueError as error:
        st.error(str(error))
        return None

    if not text.strip():
        st.error(f"Não foi possível ler {upload.name}.")
        return None

    with (
        collect_usage() as usages,
        st.spinner("Lendo o arquivo e organizando os fornecedores..."),
    ):
        result = extract_suppliers(
            text=text,
            file_name=upload.name,
        )

    log_ai_usage(
        client=client,
        usages=usages,
    )

    return {
        "file": upload.name,
        "suppliers": result.suppliers,
        "failed_parts": result.failed_parts,
        "truncated": result.truncated,
    }


def _discard_import() -> None:
    st.session_state.pop(IMPORT_KEY, None)
    st.session_state[UPLOAD_VERSION_KEY] = st.session_state.get(UPLOAD_VERSION_KEY, 0) + 1


def _show_import(
    client: Client,
    existing_keys: set[tuple[str, str, str]],
) -> None:
    imported = st.session_state.get(IMPORT_KEY)

    if imported is None:
        upload = st.file_uploader(
            "Planilha ou documento com os fornecedores",
            type=UPLOAD_TYPES,
            key=f"suppliers_upload_{st.session_state.get(UPLOAD_VERSION_KEY, 0)}",
            help=(
                "Qualquer planilha (Excel .xlsx/.xls, LibreOffice .ods, CSV), "
                "Word, PDF ou texto, até 10 MB."
            ),
        )

        if upload is not None and st.button(
            "Ler arquivo",
            icon=":material/table_view:",
            type="primary",
            key="suppliers_upload_read",
        ):
            imported = _read_upload(client=client, upload=upload)

            if imported is not None:
                st.session_state[IMPORT_KEY] = imported
                st.rerun()

        return

    st.caption(f"Arquivo: {imported['file']}")

    if imported["failed_parts"]:
        st.warning("Parte do arquivo não pôde ser organizada. Confira se falta algum fornecedor.")

    if imported["truncated"]:
        st.warning("O arquivo é grande: só a primeira parte foi lida.")

    if not imported["suppliers"]:
        st.info("Nenhum fornecedor encontrado no arquivo.")
        st.button("Escolher outro arquivo", key="suppliers_import_other", on_click=_discard_import)
        return

    st.caption("Revise antes de cadastrar: corrija o que precisar e desmarque o que não deve entrar.")
    table = supplier_frame(
        records=[
            {
                "include": supplier_key(supplier) not in existing_keys,
                **supplier,
                "already": "Sim" if supplier_key(supplier) in existing_keys else "",
            }
            for supplier in imported["suppliers"]
        ],
        columns=[*COLUMNS, "already"],
    )
    config = supplier_columns_config()
    config["already"] = st.column_config.TextColumn("Já cadastrado", disabled=True, width="small")
    edited = st.data_editor(
        table,
        column_config=config,
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
        key=f"suppliers_import_editor_{imported['file']}",
    )
    rows = [row for row in edited.to_dict(orient="records") if is_included(row)]
    save_column, discard_column = st.columns(2)

    if save_column.button(
        f"Cadastrar {len(rows)} fornecedor(es)",
        icon=":material/save:",
        type="primary",
        disabled=not rows,
        use_container_width=True,
        key="suppliers_import_save",
    ):
        _save_new(
            client=client,
            rows=rows,
            existing_keys=existing_keys,
            source_file=imported["file"],
        )

    discard_column.button(
        "Descartar",
        icon=":material/close:",
        use_container_width=True,
        key="suppliers_import_discard",
        on_click=_discard_import,
    )


# --------------------------------------------------------------------------
# Fornecedores cadastrados
# --------------------------------------------------------------------------

SELECTION_KEY = "suppliers_selection"


def _selection_state() -> dict:
    return st.session_state.setdefault(SELECTION_KEY, {"all": False, "version": 0})


def _set_selection(selected: bool) -> None:
    state = _selection_state()
    state["all"] = selected
    # Tabela nova (outra chave): a tabela de edição guarda as marcações
    # antigas e não aceitaria a troca de todas de uma vez.
    state["version"] += 1


FILTER_KEYS = {
    "suppliers_filter_country": [],
    "suppliers_filter_city": [],
    "suppliers_filter_profile": [],
    "suppliers_filter_option": [],
    "suppliers_filter_type": [],
    "suppliers_filter_service": "",
    "suppliers_filter_name": "",
    "suppliers_filter_contact": "",
}


def _clear_filters() -> None:
    """Roda antes da tela (on_click): pode zerar os campos do formulário."""
    for key, empty in FILTER_KEYS.items():
        st.session_state[key] = list(empty) if isinstance(empty, list) else empty


def _matches_service(
    wanted: str,
    supplier: dict,
) -> bool:
    """Mesmos sinônimos da ÁGORA: "hotel" acha "Hospedagem", "receptivo"
    acha "DMC"."""
    from services.fornecedores import _service_terms

    text = normalize(f"{supplier.get('service') or ''} {supplier.get('name') or ''}")
    return any(term in text for term in _service_terms(wanted))


def _filtered(suppliers: list[dict]) -> list[dict]:
    """Filtros num formulário: valem ao clicar em Buscar ou apertar Enter
    (antes, o texto só valia ao sair do campo e o "Mostrando 0 de 1" ficava
    com o filtro antigo)."""
    countries = sorted({str(item.get("country") or "") for item in suppliers} - {""})
    cities = sorted({str(item.get("city") or "") for item in suppliers} - {""})

    with st.form("suppliers_filters", border=False, enter_to_submit=True):
        country_column, city_column, option_column, type_column, profile_column = st.columns(5)
        country_column.multiselect(
            "País",
            options=countries,
            placeholder="Todos",
            key="suppliers_filter_country",
        )
        city_column.multiselect(
            "Cidade",
            options=cities,
            placeholder="Todas",
            key="suppliers_filter_city",
        )
        option_column.multiselect(
            "Opção",
            options=[f"Opção {number}" for number in OPTIONS],
            placeholder="Todas",
            key="suppliers_filter_option",
        )
        type_column.multiselect(
            "Tipo",
            options=list(PRODUCT_TYPES),
            placeholder="Todos",
            key="suppliers_filter_type",
        )
        profile_column.multiselect(
            "Perfil",
            options=list(PROFILES),
            placeholder="Todos",
            key="suppliers_filter_profile",
        )
        service_column, name_column, contact_column, search_column, clear_column = st.columns(
            [3, 3, 3, 1.3, 1.3],
            vertical_alignment="bottom",
        )
        service_column.text_input("Serviço", placeholder="Ex.: hotel, guia", key="suppliers_filter_service")
        name_column.text_input("Nome", key="suppliers_filter_name")
        contact_column.text_input("Contato", placeholder="Site, e-mail ou telefone", key="suppliers_filter_contact")
        search_column.form_submit_button(
            "Buscar",
            icon=":material/search:",
            type="primary",
            use_container_width=True,
        )
        clear_column.form_submit_button(
            "Limpar",
            icon=":material/filter_alt_off:",
            use_container_width=True,
            on_click=_clear_filters,
        )

    chosen_countries = st.session_state.get("suppliers_filter_country") or []
    chosen_cities = st.session_state.get("suppliers_filter_city") or []
    chosen_profiles = st.session_state.get("suppliers_filter_profile") or []
    chosen_options = {clean_option(value) for value in st.session_state.get("suppliers_filter_option") or []}
    chosen_types = st.session_state.get("suppliers_filter_type") or []
    service = normalize(st.session_state.get("suppliers_filter_service"))
    name = normalize(st.session_state.get("suppliers_filter_name"))
    contact = normalize(st.session_state.get("suppliers_filter_contact"))

    return [
        item
        for item in suppliers
        if (not chosen_countries or item.get("country") in chosen_countries)
        and (not chosen_cities or item.get("city") in chosen_cities)
        and (not chosen_profiles or item.get("profile") in chosen_profiles)
        and (not chosen_options or clean_option(item.get("option")) in chosen_options)
        and (not chosen_types or item.get("product_type") in chosen_types)
        and (not service or _matches_service(service, item))
        and (not name or name in normalize(item.get("name")))
        and (not contact or contact in normalize(item.get("contact")))
    ]


def _review(
    client: Client,
    rows: list[dict],
    status: str,
    note: str | None = None,
) -> int:
    done = 0

    for row in rows:
        try:
            review_supplier(
                client=client,
                supplier_id=str(row["id"]),
                supplier=row,
                status=status,
                reviewer_id=st.session_state.user_id,
                note=note,
            )
            done += 1
        except Exception:
            traceback.print_exc()

    forget_suppliers_cache()
    return done


def _show_registered(
    client: Client,
    suppliers: list[dict],
) -> None:
    st.subheader(f"Fornecedores cadastrados ({len(suppliers)})")

    if not suppliers:
        st.caption("Nenhum fornecedor cadastrado ainda.")
        return

    visible = _filtered(suppliers)

    if not visible:
        # No lugar da tabela vazia (que mostrava "empty", em inglês).
        st.info("Nenhum fornecedor encontrado com esses filtros.")
        return

    if len(visible) != len(suppliers):
        st.caption(f"Mostrando {len(visible)} de {len(suppliers)}.")

    all_column, clear_column, _space = st.columns(
        [1, 1, 3],
        vertical_alignment="center",
    )
    all_column.button(
        "Selecionar todos",
        icon=":material/select_all:",
        type="tertiary",
        key="suppliers_select_all",
        on_click=_set_selection,
        args=(True,),
    )
    clear_column.button(
        "Limpar seleção",
        icon=":material/deselect:",
        type="tertiary",
        key="suppliers_clear_selection",
        on_click=_set_selection,
        args=(False,),
    )
    state = _selection_state()
    table = supplier_frame(
        records=[
            {"include": state["all"], **{name: item.get(name) for name in FIELDS}, "id": str(item["id"])}
            for item in visible
        ],
        columns=[*COLUMNS, "id"],
    )
    config = supplier_columns_config()
    config["include"] = st.column_config.CheckboxColumn("Selecionar", width="small")
    filters = "_".join(
        str(st.session_state.get(key, ""))
        for key in (
            "suppliers_filter_country",
            "suppliers_filter_city",
            "suppliers_filter_profile",
            "suppliers_filter_option",
            "suppliers_filter_type",
            "suppliers_filter_service",
            "suppliers_filter_name",
            "suppliers_filter_contact",
        )
    )
    edited = st.data_editor(
        table,
        column_config=config,
        column_order=list(COLUMNS),
        hide_index=True,
        width="stretch",
        key=f"suppliers_registered_editor_{abs(hash(filters))}_{state['version']}",
    )
    original = {row["id"]: _clean_row(row) for row in table.to_dict(orient="records")}
    rows = edited.to_dict(orient="records")
    changed = [row for row in rows if _clean_row(row) != original.get(row["id"])]
    selected = [row for row in rows if row.get("include")]
    save_column, remove_column = st.columns(2)

    if save_column.button(
        f"Salvar alterações ({len(changed)})",
        icon=":material/save:",
        disabled=not changed,
        use_container_width=True,
        key="suppliers_save",
    ):
        changed_ids = {row["id"] for row in changed}
        # A edição não pode transformar um fornecedor em cópia de outro.
        _new_rows, duplicates = split_duplicates(
            rows=changed,
            existing_keys={
                supplier_key(item)
                for item in suppliers
                if str(item["id"]) not in changed_ids
            },
        )

        if incomplete_rows(changed):
            st.error("Preencha nome, serviço e país em todas as linhas alteradas.")
        elif duplicates:
            names = ", ".join(str(row.get("name")) for row in duplicates[:5])
            st.error(f"Já existe outro fornecedor com o mesmo nome, cidade e país: {names}.")
        else:
            done = _review(client=client, rows=changed, status=STATUS_APPROVED)
            _flash(f"{done} fornecedor(es) atualizado(s).")

    if remove_column.button(
        f"Retirar selecionados ({len(selected)})",
        icon=":material/delete:",
        disabled=not selected,
        use_container_width=True,
        key="suppliers_remove",
    ):
        done = _review(
            client=client,
            rows=selected,
            status=STATUS_REJECTED,
            note="Retirado do cadastro.",
        )
        _flash(f"{done} fornecedor(es) retirado(s) do cadastro.")


# --------------------------------------------------------------------------
# Aba
# --------------------------------------------------------------------------


def show_suppliers_section(client: Client) -> None:
    message = st.session_state.pop(FLASH_KEY, None)

    if message:
        st.success(message)

    try:
        suppliers = list_suppliers(client=client, status=STATUS_APPROVED)
    except Exception as error:
        print(f"[FORNECEDORES] Falha ao carregar: {type(error).__name__}", flush=True)
        st.error(
            "Não foi possível carregar os fornecedores. Confira se a "
            "migração 015 foi aplicada no Supabase."
        )
        return

    existing_keys = {supplier_key(item) for item in suppliers}

    st.subheader("Cadastrar fornecedores")
    st.caption(
        CARD_NOTE
        + " O que for cadastrado aqui já passa a ser usado pela ÁGORA para a "
        "equipe de Operações."
    )
    mode = st.segmented_control(
        "Como cadastrar",
        options=["Cadastrar", "Importar planilha"],
        default="Cadastrar",
        key="suppliers_new_mode",
        label_visibility="collapsed",
    )

    if mode == "Importar planilha":
        _show_import(client=client, existing_keys=existing_keys)
    else:
        _show_manual_form(client=client, existing_keys=existing_keys)

    st.divider()
    _show_registered(client=client, suppliers=suppliers)
