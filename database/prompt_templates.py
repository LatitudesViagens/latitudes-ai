"""Modelos de prompt (migração 009). Todos leem os ativos; só o TI cria,
edita e apaga (políticas RLS)."""

from supabase import Client

TEMPLATE_COLUMNS = "id,title,description,content,is_active,sort_order,updated_at"


def list_active_templates(client: Client) -> list[dict]:
    response = (
        client.table("prompt_templates")
        .select(TEMPLATE_COLUMNS)
        .eq("is_active", True)
        .order("sort_order")
        .order("title")
        .execute()
    )
    return response.data or []


def list_all_templates(client: Client) -> list[dict]:
    response = (
        client.table("prompt_templates")
        .select(TEMPLATE_COLUMNS)
        .order("sort_order")
        .order("title")
        .execute()
    )
    return response.data or []


def create_template(
    client: Client,
    title: str,
    description: str,
    content: str,
    sort_order: int,
) -> dict:
    response = (
        client.table("prompt_templates")
        .insert(
            {
                "title": title.strip(),
                "description": description.strip() or None,
                "content": content.strip(),
                "sort_order": int(sort_order),
            }
        )
        .execute()
    )
    return response.data[0]


def update_template(
    client: Client,
    template_id: str,
    editor_id: str,
    title: str,
    description: str,
    content: str,
    sort_order: int,
    is_active: bool,
) -> None:
    response = (
        client.table("prompt_templates")
        .update(
            {
                "title": title.strip(),
                "description": description.strip() or None,
                "content": content.strip(),
                "sort_order": int(sort_order),
                "is_active": bool(is_active),
                "updated_by": str(editor_id),
            }
        )
        .eq("id", str(template_id))
        .execute()
    )

    if not response.data:
        raise RuntimeError("Modelo não encontrado ou sem permissão.")


def delete_template(
    client: Client,
    template_id: str,
) -> None:
    client.table("prompt_templates").delete().eq(
        "id",
        str(template_id),
    ).execute()
