"""Fornecedores da base de conhecimento (migração 014). As políticas RLS
garantem: qualquer pessoa só SUGERE (entra como pendente); só o TI aprova,
edita, rejeita e apaga; todos leem os aprovados."""

from datetime import datetime, timezone

from supabase import Client

STATUS_PENDING = "pendente"
STATUS_APPROVED = "aprovada"
STATUS_REJECTED = "rejeitada"

# Campos da ficha (combinados com a Isabelle em 20/10/2026).
FIELDS = ("name", "city", "country", "stars", "service", "contact")

SUPPLIER_COLUMNS = (
    "id,name,city,country,stars,service,contact,status,source_file,"
    "source_message_id,suggested_by,reviewed_by,reviewed_at,created_at"
)


def clean_stars(value) -> int | None:
    """Estrelas de 1 a 5; o resto (vazio, "4*", 4.0 da tabela) é tratado."""
    try:
        stars = int(float(str(value).strip().rstrip("*★ ").replace(",", ".")))
    except (TypeError, ValueError):
        return None

    return stars if 1 <= stars <= 5 else None


def _record(supplier: dict) -> dict:
    """Só os campos da ficha, limpos (texto vazio vira nulo)."""
    record = {}

    for name in FIELDS:
        value = supplier.get(name)

        if name == "stars":
            record[name] = clean_stars(value)
            continue

        # Célula vazia da tabela de edição chega como NaN (float).
        if isinstance(value, float) and value != value:
            value = None

        text = " ".join(str(value or "").split())
        record[name] = text or None

    return record


def suggest_suppliers(
    client: Client,
    suppliers: list[dict],
    source_message_id: str | None,
    source_file: str | None,
) -> int:
    """Envia as fichas para aprovação do TI (todas pendentes)."""
    records = []

    for supplier in suppliers:
        record = _record(supplier)
        record["status"] = STATUS_PENDING
        record["source_file"] = (source_file or "")[:200] or None

        if source_message_id:
            record["source_message_id"] = str(source_message_id)

        records.append(record)

    if not records:
        return 0

    response = client.table("suppliers").insert(records).execute()
    return len(response.data or [])


def has_suggestions_for_message(
    client: Client,
    source_message_id: str,
) -> bool:
    response = (
        client.table("suppliers")
        .select("id")
        .eq("source_message_id", str(source_message_id))
        .limit(1)
        .execute()
    )
    return bool(response.data)


def list_suppliers(
    client: Client,
    status: str,
    limit: int = 2000,
) -> list[dict]:
    response = (
        client.table("suppliers")
        .select(SUPPLIER_COLUMNS)
        .eq("status", status)
        .order("country")
        .order("city")
        .order("name")
        .limit(limit)
        .execute()
    )
    return response.data or []


def list_approved_suppliers(client: Client) -> list[dict]:
    """Fornecedores aprovados, para a consulta da ÁGORA (na memória do
    servidor, services/fornecedores.py)."""
    response = (
        client.table("suppliers")
        .select("id,name,city,country,stars,service,contact")
        .eq("status", STATUS_APPROVED)
        .limit(5000)
        .execute()
    )
    return response.data or []


def create_approved_supplier(
    client: Client,
    supplier: dict,
    reviewer_id: str,
) -> None:
    """Fornecedor incluído direto pelo TI (já aprovado)."""
    record = _record(supplier)
    record.update(
        {
            "status": STATUS_APPROVED,
            "reviewed_by": str(reviewer_id),
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    client.table("suppliers").insert(record).execute()


def review_supplier(
    client: Client,
    supplier_id: str,
    supplier: dict,
    status: str,
    reviewer_id: str,
    note: str | None = None,
) -> None:
    """Aprova (com as edições do TI), rejeita ou retira um fornecedor."""
    record = _record(supplier) if status == STATUS_APPROVED else {}
    record.update(
        {
            "status": status,
            "reviewed_by": str(reviewer_id),
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "review_note": (note or "").strip() or None,
        }
    )
    response = (
        client.table("suppliers")
        .update(record)
        .eq("id", str(supplier_id))
        .execute()
    )

    if not response.data:
        raise RuntimeError("Fornecedor não encontrado ou sem permissão.")
