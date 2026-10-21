"""Fornecedores (migrações 014 e 015). As políticas RLS garantem: só a
equipe de Operações e o TI leem; só coordenadores de Operações e o TI
cadastram (já aprovado), editam e retiram."""

from datetime import datetime, timezone

from supabase import Client

STATUS_PENDING = "pendente"
STATUS_APPROVED = "aprovada"
STATUS_REJECTED = "rejeitada"

# Campos da ficha (combinados com a Isabelle em 20/10/2026; perfil na 015,
# trocado na 016). Estrelas saíram da ficha na 016: a coluna continua no
# banco, sem uso.
FIELDS = (
    "name",
    "city",
    "country",
    "option",
    "product_type",
    "profile",
    "service",
    "contact",
    "notes",
)

SUPPLIER_COLUMNS = (
    "id,name,city,country,option,product_type,profile,service,contact,notes,"
    "status,source_file,source_message_id,suggested_by,reviewed_by,"
    "reviewed_at,created_at"
)

# Opção 1, 2 ou 3 e tipo de produto (migração 017).
OPTIONS = (1, 2, 3)
PRODUCT_TYPES = ("GRP", "PVT", "FIT")
PRODUCT_TYPE_SYNONYMS = {
    "GRP": ("grp", "grupo", "grupos", "group"),
    "PVT": ("pvt", "privativo", "privado", "private", "exclusivo"),
    "FIT": ("fit", "individual", "independente"),
}
MAX_NOTES = 1000

# Perfis do fornecedor (mesma lista da restrição da migração 016; trocar um
# nome exige trocar lá também).
PROFILES = ("VIP", "BUDGET", "SERVIÇOS SEPARADOS")

# Outras palavras para os mesmos perfis (sem acento, minúsculas).
PROFILE_SYNONYMS = {
    # Radicais: "separado(s)/separada(s)", "avulso(s)/avulsa(s)".
    "SERVIÇOS SEPARADOS": ("separad", "avuls", "a la carte", "sob demanda"),
    "VIP": ("vip", "luxo", "luxuoso", "luxury", "premium", "boutique", "exclusivo", "alto padrao", "superior", "executivo"),
    "BUDGET": ("budget", "economico", "simples", "basico", "baixo custo", "hostel", "estudantes", "custo beneficio"),
}


def _plain(text) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.lower().replace("-", " ").split())


def clean_profile(value) -> str | None:
    """Um dos PROFILES ("vip", "luxo", "econômico", "avulso" → nome
    oficial); o resto (vazio, "Não informado", texto sem perfil) vira nulo."""
    if value is None or (isinstance(value, float) and value != value):
        return None

    plain = _plain(value)

    if not plain:
        return None

    for profile in PROFILES:
        if plain == _plain(profile):
            return profile

    # "Serviços separados" primeiro: é o modelo de venda, vale mesmo se o
    # texto também falar em luxo ou economia.
    for profile in PROFILE_SYNONYMS:
        if any(word in plain for word in PROFILE_SYNONYMS[profile]):
            return profile

    return None


def _is_empty(value) -> bool:
    # Célula vazia da tabela de edição chega como NaN (float).
    return value is None or (isinstance(value, float) and value != value)


def clean_option(value) -> int | None:
    """Opção 1, 2 ou 3 ("1", "Opção 2", "op. 3", 2.0 da tabela); o resto
    vira nulo."""
    if _is_empty(value) or isinstance(value, bool):
        return None

    import re

    match = re.search(r"\d+", str(value))
    number = int(match.group(0)) if match else None
    return number if number in OPTIONS else None


def clean_product_type(value) -> str | None:
    """GRP, PVT ou FIT ("grupo", "privativo", "individual" também valem)."""
    if _is_empty(value):
        return None

    plain = _plain(value)

    for product_type, words in PRODUCT_TYPE_SYNONYMS.items():
        if plain == product_type.lower() or any(word in plain.split() for word in words):
            return product_type

    return None


def clean_notes(value) -> str | None:
    """Observação livre: mantém as quebras de linha, tira espaços sobrando."""
    if _is_empty(value):
        return None

    lines = [" ".join(line.split()) for line in str(value).strip().splitlines()]
    text = "\n".join(lines).strip()
    return text[:MAX_NOTES] or None


def _record(supplier: dict) -> dict:
    """Só os campos da ficha, limpos (texto vazio vira nulo)."""
    record = {}

    for name in FIELDS:
        value = supplier.get(name)

        if name == "profile":
            record[name] = clean_profile(value)
            continue

        if name == "option":
            record[name] = clean_option(value)
            continue

        if name == "product_type":
            record[name] = clean_product_type(value)
            continue

        if name == "notes":
            record[name] = clean_notes(value)
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
        .select("id,name,city,country,option,product_type,profile,service,contact,notes")
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
