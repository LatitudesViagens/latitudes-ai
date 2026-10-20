"""Fornecedores na aba Base de conhecimento do Painel de controle.

O TI revisa as sugestões (aprova com edições ou rejeita), edita ou retira
os aprovados e inclui fornecedores direto. Toda mudança limpa a lista da
memória do servidor (a ÁGORA passa a usar na próxima pergunta).
"""

import traceback

import pandas
import streamlit as st
from supabase import Client

from admin.cache import user_emails
from admin import supabase_admin
from database.suppliers import (
    FIELDS,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    clean_stars,
    create_approved_supplier,
    list_suppliers,
    review_supplier,
)
from services.fornecedores import forget_suppliers_cache, split_duplicates, supplier_key
from ui.sugerir_fornecedores import COLUMNS, incomplete_rows, supplier_columns_config, supplier_frame

FLASH_KEY = "admin_suppliers_flash"


def _flash(message: str) -> None:
    st.session_state[FLASH_KEY] = message

    # Depois de aprovar/rejeitar/retirar, a próxima lista começa sem seleção.
    for table_name in ("pending", "approved"):
        _set_selection(table_name, selected=False)

    st.rerun()


def _selection_state(table_name: str) -> dict:
    return st.session_state.setdefault(
        f"suppliers_selection_{table_name}",
        {"all": False, "version": 0},
    )


def _set_selection(
    table_name: str,
    selected: bool,
) -> None:
    state = _selection_state(table_name)
    state["all"] = selected
    # Tabela nova (outra chave): a tabela de edição guarda as marcações
    # antigas e não aceitaria a troca de todas de uma vez.
    state["version"] += 1


def _selection_bar(table_name: str) -> tuple[bool, int]:
    """"Selecionar todos" e "Limpar seleção" acima da tabela; marcar um por
    um continua na própria tabela. Devolve (todos marcados?, versão)."""
    all_column, clear_column, _space = st.columns(
        [1, 1, 3],
        vertical_alignment="center",
    )
    all_column.button(
        "Selecionar todos",
        icon=":material/select_all:",
        type="tertiary",
        key=f"suppliers_select_all_{table_name}",
        on_click=_set_selection,
        args=(table_name, True),
    )
    clear_column.button(
        "Limpar seleção",
        icon=":material/deselect:",
        type="tertiary",
        key=f"suppliers_clear_{table_name}",
        on_click=_set_selection,
        args=(table_name, False),
    )
    state = _selection_state(table_name)
    return state["all"], state["version"]


def _emails(client: Client) -> dict[str, str]:
    if not supabase_admin.is_configured():
        return {}

    try:
        return user_emails(client)
    except Exception as error:
        print(f"[TI] Falha ao listar e-mails: {type(error).__name__}", flush=True)
        return {}


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


def _clean_row(row: dict) -> tuple:
    """Valores comparáveis: célula vazia (NaN) = vazio e 4.0 = 4 estrelas."""
    values = []

    for name in FIELDS:
        value = row.get(name)

        if name == "stars":
            values.append(clean_stars(value))
        elif value is None or (isinstance(value, float) and value != value):
            values.append("")
        else:
            values.append(" ".join(str(value).split()))

    return tuple(values)


def _table(
    suppliers: list[dict],
    extra: dict[str, list],
    selected: bool = False,
) -> pandas.DataFrame:
    records = []

    for index, item in enumerate(suppliers):
        record = {"include": selected, **{name: item.get(name) for name in FIELDS}}
        record.update({name: values[index] for name, values in extra.items()})
        record["id"] = str(item["id"])
        records.append(record)

    # Tipos certos por coluna (coluna toda vazia não vira número).
    return supplier_frame(
        records=records,
        columns=[*COLUMNS, *extra, "id"],
    )


def _show_pending(
    client: Client,
    pending: list[dict],
    approved_keys: set[tuple[str, str, str]],
) -> None:
    st.subheader(f"Fornecedores aguardando aprovação ({len(pending)})")

    if not pending:
        st.caption("Nenhuma sugestão pendente.")
        return

    st.caption(
        "Marque os que vão entrar, corrija o que precisar e aprove. O que "
        "for aprovado fica visível para todos que usam a ÁGORA."
    )
    emails = _emails(client)
    select_all, version = _selection_bar("pending")
    table = _table(
        suppliers=pending,
        extra={
            "already": [
                "Sim" if supplier_key(item) in approved_keys else ""
                for item in pending
            ],
            "author": [emails.get(str(item.get("suggested_by")), "") for item in pending],
            "source": [item.get("source_file") or "" for item in pending],
        },
        selected=select_all,
    )
    config = supplier_columns_config()
    config["include"] = st.column_config.CheckboxColumn("Selecionar", width="small")
    config["already"] = st.column_config.TextColumn("Já na base", disabled=True, width="small")
    config["author"] = st.column_config.TextColumn("Sugerido por", disabled=True)
    config["source"] = st.column_config.TextColumn("Arquivo", disabled=True)

    edited = st.data_editor(
        table,
        column_config=config,
        column_order=[*COLUMNS, "already", "author", "source"],
        hide_index=True,
        width="stretch",
        key=f"suppliers_pending_editor_{version}",
    )
    selected = [row for row in edited.to_dict(orient="records") if row.get("include")]
    approve_column, reject_column = st.columns(2)

    if approve_column.button(
        f"Aprovar selecionados ({len(selected)})",
        icon=":material/check:",
        type="primary",
        disabled=not selected,
        use_container_width=True,
        key="suppliers_approve",
    ):
        missing = incomplete_rows(selected)

        if missing:
            st.error(f"Preencha nome, país e serviço em {missing} linha(s).")
        else:
            # Já na base (ou repetido na seleção): não entra duas vezes.
            new_rows, duplicates = split_duplicates(
                rows=selected,
                existing_keys=approved_keys,
            )
            done = _review(client=client, rows=new_rows, status=STATUS_APPROVED)
            _review(
                client=client,
                rows=duplicates,
                status=STATUS_REJECTED,
                note="Duplicado: já está na base.",
            )
            message = f"{done} fornecedor(es) aprovado(s) e incluído(s) na base."

            if duplicates:
                message += f" {len(duplicates)} já estava(m) na base e foi(ram) descartado(s) como duplicado(s)."

            _flash(message)

    if reject_column.button(
        f"Rejeitar selecionados ({len(selected)})",
        icon=":material/close:",
        disabled=not selected,
        use_container_width=True,
        key="suppliers_reject",
    ):
        done = _review(client=client, rows=selected, status=STATUS_REJECTED)
        _flash(f"{done} sugestão(ões) rejeitada(s).")


def _show_approved(
    client: Client,
    approved: list[dict],
) -> None:
    all_approved = approved
    st.subheader(f"Fornecedores na base ({len(approved)})")

    if not approved:
        st.caption("Nenhum fornecedor aprovado ainda.")
        return

    search = st.text_input(
        "Filtrar",
        placeholder="País, cidade, nome ou serviço",
        key="suppliers_filter",
    )

    if search.strip():
        from services.fornecedores import normalize

        wanted = normalize(search)
        approved = [
            item
            for item in approved
            if wanted in normalize(" ".join(str(item.get(name) or "") for name in FIELDS))
        ]

    # "Selecionar todos" vale para o que o filtro está mostrando.
    select_all, version = _selection_bar("approved")
    table = _table(
        suppliers=approved,
        extra={},
        selected=select_all,
    )
    config = supplier_columns_config()
    config["include"] = st.column_config.CheckboxColumn("Selecionar", width="small")
    edited = st.data_editor(
        table,
        column_config=config,
        column_order=list(COLUMNS),
        hide_index=True,
        width="stretch",
        key=f"suppliers_approved_editor_{search.strip()}_{version}",
    )
    original = {row["id"]: _clean_row(row) for row in table.to_dict(orient="records")}
    rows = edited.to_dict(orient="records")
    changed = [
        row
        for row in rows
        if _clean_row(row) != original.get(row["id"])
    ]
    selected = [row for row in rows if row.get("include")]
    save_column, remove_column = st.columns(2)

    if save_column.button(
        f"Salvar alterações ({len(changed)})",
        icon=":material/save:",
        disabled=not changed,
        use_container_width=True,
        key="suppliers_save",
    ):
        missing = incomplete_rows(changed)
        changed_ids = {row["id"] for row in changed}
        # A edição não pode transformar um fornecedor em cópia de outro.
        _new_rows, duplicates = split_duplicates(
            rows=changed,
            existing_keys={
                supplier_key(item)
                for item in all_approved
                if str(item["id"]) not in changed_ids
            },
        )

        if missing:
            st.error(f"Preencha nome, país e serviço em {missing} linha(s).")
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
            note="Retirado da base pelo TI.",
        )
        _flash(f"{done} fornecedor(es) retirado(s) da base.")


def _show_new_supplier_form(
    client: Client,
    approved_keys: set[tuple[str, str, str]],
) -> None:
    st.subheader("Novo fornecedor")

    with st.form("suppliers_new_form", clear_on_submit=True, border=True):
        name_column, service_column = st.columns(2)
        name = name_column.text_input("Nome")
        service = service_column.text_input("Serviço", placeholder="Ex.: Hotel, Receptivo (DMC), Guia")
        city_column, country_column, stars_column = st.columns([2, 2, 1])
        city = city_column.text_input("Cidade")
        country = country_column.text_input("País")
        stars = stars_column.selectbox("Estrelas", options=["", "1", "2", "3", "4", "5"])
        contact = st.text_input("Contato geral", placeholder="Site, e-mail ou telefone da empresa")
        submitted = st.form_submit_button("Incluir na base", type="primary")

    if not submitted:
        return

    supplier = {
        "name": name,
        "city": city,
        "country": country,
        "stars": stars,
        "service": service,
        "contact": contact,
    }

    if incomplete_rows([supplier]):
        st.warning("Preencha nome, país e serviço.")
        return

    if supplier_key(supplier) in approved_keys:
        st.warning("Este fornecedor (mesmo nome, cidade e país) já está na base.")
        return

    try:
        create_approved_supplier(
            client=client,
            supplier=supplier,
            reviewer_id=st.session_state.user_id,
        )
    except Exception:
        traceback.print_exc()
        st.error("Não foi possível incluir. Tente novamente.")
        return

    forget_suppliers_cache()
    _flash("Fornecedor incluído na base.")


def show_suppliers_section(client: Client) -> None:
    message = st.session_state.pop(FLASH_KEY, None)

    if message:
        st.success(message)

    try:
        pending = list_suppliers(client=client, status=STATUS_PENDING)
        approved = list_suppliers(client=client, status=STATUS_APPROVED)
    except Exception as error:
        print(f"[TI] Falha ao carregar fornecedores: {type(error).__name__}", flush=True)
        st.error(
            "Não foi possível carregar os fornecedores. Confira se a "
            "migração 014 foi aplicada no Supabase."
        )
        return

    approved_keys = {supplier_key(item) for item in approved}
    _show_pending(
        client=client,
        pending=pending,
        approved_keys=approved_keys,
    )
    st.divider()
    _show_approved(client=client, approved=approved)
    st.divider()
    _show_new_supplier_form(
        client=client,
        approved_keys=approved_keys,
    )
