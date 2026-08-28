import os
from pathlib import Path

from dotenv import load_dotenv
from tavily import TavilyClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "latitudes_agent" / ".env"

load_dotenv(dotenv_path=ENV_FILE)


def search_web(
    query: str,
    max_results: int = 5,
) -> dict:
    """Pesquisa informações públicas e atuais na internet."""

    clean_query = query.strip()

    if not clean_query:
        raise ValueError("A pesquisa não pode estar vazia.")

    if not 1 <= max_results <= 8:
        raise ValueError(
            "A quantidade de resultados deve estar entre 1 e 8."
        )

    api_key = os.getenv("TAVILY_API_KEY")

    if not api_key:
        raise RuntimeError(
            "A variável TAVILY_API_KEY não foi configurada."
        )

    client = TavilyClient(api_key=api_key)

    response = client.search(
        query=clean_query,
        search_depth="basic",
        topic="general",
        max_results=max_results,
        include_answer=False,
        include_raw_content=False,
        include_images=False,
        timeout=30,
    )

    results = []

    for item in response.get("results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "score": item.get("score"),
            }
        )

    return {
        "query": clean_query,
        "results": results,
    }