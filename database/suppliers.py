"""Fornecedores (migrações 014 e 015). As políticas RLS garantem: só a
equipe de Operações e o TI leem; só coordenadores de Operações e o TI
cadastram (já aprovado), editam e retiram."""

from datetime import datetime, timezone

from supabase import Client

STATUS_PENDING = "pendente"
STATUS_APPROVED = "aprovada"
STATUS_REJECTED = "rejeitada"

# Campos da ficha (combinados com a Isabelle em 20/10/2026; perfil na 015).
FIELDS = ("name", "city", "country", "stars", "profile", "service", "contact")

SUPPLIER_COLUMNS = (
    "id,name,city,country,stars,profile,service,contact,status,source_file,"
    "source_message_id,suggested_by,reviewed_by,reviewed_at,created_at"
)

# Perfis do fornecedor, do mais caro ao mais simples (mesma lista da
# restrição da migração 015; trocar um nome exige trocar lá também).
PROFILES = ("Luxo", "Superior", "Intermediário", "Econômico")

# Outras palavras para os mesmos perfis (sem acento, minúsculas).
PROFILE_SYNONYMS = {
    "Luxo": ("luxo", "luxuoso", "luxury", "premium", "boutique", "exclusivo"),
    "Superior": ("superior", "alto padrao", "primeira linha", "executivo", "sofisticado"),
    "Intermediário": ("intermediario", "medio", "conforto", "turistico", "custo beneficio"),
    "Econômico": ("economico", "simples", "basico", "budget", "hostel", "estudantes", "baixo custo"),
}


def clean_stars(value) -> int | None:
    """Estrelas de 1 a 5; o resto (vazio, "4*", 4.0 da tabela) é tratado."""
    try:
        stars = int(float(str(value).strip().rstrip("*★ ").replace(",", ".")))
    except (TypeError, ValueError):
        return None

    return stars if 1 <= stars <= 5 else None


def _plain(text) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.lower().replace("-", " ").split())


def clean_profile(value) -> str | None:
    """Um dos PROFILES ("luxo", "Premium", "econômico" → nome oficial); o
    resto (vazio, "Não informado", texto sem perfil) vira nulo."""
    if value is None or (isinstance(value, float) and value != value):
        return None

    plain = _plain(value)

    if not plain:
        return None

    for profile in PROFILES:
        if plain == _plain(profile):
            return profile

    # Do mais caro ao mais simples: "alto padrão" é Superior, não outro.
    for profile in PROFILES:
        if any(word in plain for word in PROFILE_SYNONYMS[profile]):
            return profile

    return None


def _record(supplier: dict) -> dict:
    """Só os campos da ficha, limpos (texto vazio vira nulo)."""
    record = {}

    for name in FIELDS:
        value = supplier.get(name)

        if name == "stars":
            record[name] = clean_stars(value)
            continue

        if name == "profile":
            record[name] = clean_profile(value)
            continue

        # Célula vazia da tabela de edição chega como NaN (float).
        if isinstance(value, float) and value != value:
            value = None

        text = " ".join(str(value or "").split())
        record[name] = text or None

    return record


def create_suppliers(
    client: Client,
    suppliers: list[dict],
    reviewer_id: str,
    source_file: str | None = None,
) -> int:
    """Cadastra fornecedores já aprovados (coordenadores de Operações e TI;
    o RLS da migração 015 barra os outros). Um envio só, para a planilha."""
    reviewed_at = datetime.now(timezone.utc).isoformat()
    records = []

    for supplier in suppliers:
        record = _record(supplier)
        record.update(
            {
                "status": STATUS_APPROVED,
                "source_file": (source_file or "")[:200] or None,
                "reviewed_by": str(reviewer_id),
                "reviewed_at": reviewed_at,
            }
        )
        records.append(record)

    if not records:
        return 0

    response = client.table("suppliers").insert(records).execute()
    return len(response.data or [])


def list_suppliers(
    client: Client,
    status: str = STATUS_APPROVED,
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
        .select("id,name,city,country,stars,profile,service,contact")
        .eq("status", STATUS_APPROVED)
        .limit(5000)
        .execute()
    )
    return response.data or []


def review_supplier(
    client: Client,
    supplier_id: str,
    supplier: dict,
    status: str,
    reviewer_id: str,
    note: str | None = None,
) -> None:
    """Salva a edição (status aprovado) ou retira um fornecedor da base
    (status rejeitado, com a anotação; o histórico fica)."""
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
