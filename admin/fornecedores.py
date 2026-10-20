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
from database.attachments import extract_file_text, validate_text_file
from database.suppliers import (
    FIELDS,
    PROFILES,
    STATUS_APPROVED,
    STATUS_REJECTED,
    clean_profile,
    clean_stars,
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
    "stars": "Estrelas",
    "profile": "Perfil",
    "service": "Serviço",
    "contact": "Contato geral",
}
TEXT_COLUMNS = ("name", "city", "country", "profile", "service", "contact")

STAR_OPTIONS = ["Sem classificação", "1", "2", "3", "4", "5"]
NO_PROFILE = "Não informado"
PROFILE_OPTIONS = [NO_PROFILE, *PROFILES]
PROFILE_HELP = (
    "Luxo: premium/boutique. Superior: alto padrão. Intermediário: bom "
    "custo-benefício. Econômico: grupos mais simples, estudantes."
)
CARD_NOTE = (
    "A ficha leva só nome, cidade, país, estrelas, perfil, serviço e contato "
    "geral da empresa (sem preços, propostas ou contatos pessoais)."
)
UPLOAD_TYPES = ["xlsx", "csv", "docx", "pdf", "txt"]


# --------------------------------------------------------------------------
# Tabelas e regras comuns
# --------------------------------------------------------------------------


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
        "profile": st.column_config.SelectboxColumn(
            COLUMNS["profile"],
            options=list(PROFILES),
            help=PROFILE_HELP,
        ),
        "service": st.column_config.TextColumn(COLUMNS["service"], required=True, max_chars=300),
        "contact": st.column_config.TextColumn(COLUMNS["contact"], max_chars=300),
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
                "" if value is None or (isinstance(value, float) and value != value) else str(value)
                for value in table[name]
            ]
            table[name] = table[name].astype("object")
        elif name == "stars":
            table[name] = pandas.to_numeric(table[name], errors="coerce").astype("float64")
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
    """Valores comparáveis: célula vazia (NaN) = vazio e 4.0 = 4 estrelas."""
    values = []

    for name in FIELDS:
        value = row.get(name)

        if name == "stars":
            values.append(clean_stars(value))
        elif name == "profile":
            values.append(clean_profile(value))
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
            stars_column, profile_column = st.columns(2)
            stars = stars_column.selectbox("Estrelas", options=STAR_OPTIONS, key=f"{prefix}_stars")
            profile = profile_column.selectbox(
                "Perfil",
                options=PROFILE_OPTIONS,
                help=PROFILE_HELP,
                key=f"{prefix}_profile",
            )
            contact = st.text_input(
                "Contato geral",
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
                "profile": clean_profile(profile),
                "contact": contact,
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


def _read_upload(
    client: Client,
    upload,
) -> dict | None:
    from services.fornecedores import extract_suppliers

    data = upload.getvalue()

    try:
        mime_type = validate_text_file(
            filename=upload.name,
            declared_mime_type=upload.type or "",
            data=data,
        )
        text = extract_file_text(
            data=data,
            mime_type=mime_type,
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
            help="Excel, CSV, Word, PDF ou texto, até 10 MB.",
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


def _filtered(suppliers: list[dict]) -> list[dict]:
    """Filtros: país, cidade, perfil (listas) e serviço/nome (texto)."""
    countries = sorted({str(item.get("country") or "") for item in suppliers} - {""})
    cities = sorted({str(item.get("city") or "") for item in suppliers} - {""})
    country_column, city_column, profile_column = st.columns(3)
    chosen_countries = country_column.multiselect(
        "País",
        options=countries,
        placeholder="Todos",
        key="suppliers_filter_country",
    )
    chosen_cities = city_column.multiselect(
        "Cidade",
        options=cities,
        placeholder="Todas",
        key="suppliers_filter_city",
    )
    chosen_profiles = profile_column.multiselect(
        "Perfil",
        options=list(PROFILES),
        placeholder="Todos",
        key="suppliers_filter_profile",
    )
    service_column, name_column = st.columns(2)
    service = normalize(service_column.text_input("Serviço", placeholder="Ex.: hotel, guia", key="suppliers_filter_service"))
    name = normalize(name_column.text_input("Nome", key="suppliers_filter_name"))

    return [
        item
        for item in suppliers
        if (not chosen_countries or item.get("country") in chosen_countries)
        and (not chosen_cities or item.get("city") in chosen_cities)
        and (not chosen_profiles or item.get("profile") in chosen_profiles)
        and (not service or service in normalize(item.get("service")))
        and (not name or name in normalize(item.get("name")))
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
            "suppliers_filter_service",
            "suppliers_filter_name",
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
