from supabase import Client


def search_conversations(
    client: Client,
    search_text: str,
    max_results: int = 20,
) -> list[dict]:
    """Busca de texto em português nas conversas de quem está logado.

    Função public.search_conversations (migração 009): ignora acentos, aceita
    "frase exata" e -palavra, e o RLS garante que só as próprias conversas
    aparecem. Devolve conversation_id, title, snippet (trecho com **termos**
    marcados), rank e last_match_at.
    """
    response = client.rpc(
        "search_conversations",
        {
            "search_text": search_text,
            "max_results": max_results,
        },
    ).execute()
    return response.data or []
