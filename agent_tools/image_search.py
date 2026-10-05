import asyncio
from contextvars import ContextVar
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from dotenv import load_dotenv
from tavily import TavilyClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "latitudes_agent" / ".env"

load_dotenv(dotenv_path=ENV_FILE)


MAX_IMAGES = 6

# Trava: só há busca de imagens quando a mensagem atual pede fotos/imagens.
# services/agent_runner.py define o valor a cada mensagem.
IMAGES_REQUESTED: ContextVar[bool] = ContextVar(
    "images_requested",
    default=False,
)

IMAGE_REQUEST_PATTERN = re.compile(
    r"\b(?:fotos?|fotografias?|imagens?|imagem|figuras?|galeria)\b",
    flags=re.IGNORECASE,
)

# Links que não são imagens diretas (páginas, widgets de redes sociais).
_BLOCKED_HOSTS = (
    "instagram.com",
    "facebook.com",
    "fbsbx.com",
    "tiktok.com",
    "google.com",
    "gstatic.com",
    "pinterest.com",
)


# Quantas fotos ainda podem ser mostradas nesta resposta (somando todas as
# buscas). services/agent_runner.py define o valor a cada tentativa.
IMAGE_BUDGET: ContextVar[list[int] | None] = ContextVar(
    "image_budget",
    default=None,
)

_NUMBER_WORDS = {
    "um": 1,
    "uma": 1,
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "três": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
}
_IMAGE_COUNT_PATTERN = re.compile(
    r"\b(\d{1,2}|uma?|dois|duas|tr[êe]s|quatro|cinco|seis)\s+"
    r"(?:\w+\s+)?(?:fotos?|fotografias?|imagens?|imagem)\b",
    flags=re.IGNORECASE,
)


def user_requested_images(message: str) -> bool:
    return IMAGE_REQUEST_PATTERN.search(message or "") is not None


def requested_image_count(message: str) -> int:
    """Total de fotos pedido na mensagem ("duas imagens" → 2); padrão 6."""
    match = _IMAGE_COUNT_PATTERN.search(message or "")

    if match is None:
        return MAX_IMAGES

    value = match.group(1).lower()
    count = int(value) if value.isdigit() else _NUMBER_WORDS.get(value, MAX_IMAGES)
    return max(1, min(count, MAX_IMAGES))


def _is_usable_image_url(url: str) -> bool:
    parsed = urlparse(url)

    if parsed.scheme != "https" or not parsed.netloc:
        return False

    host = parsed.netloc.lower()
    return not any(
        host == blocked or host.endswith(f".{blocked}")
        for blocked in _BLOCKED_HOSTS
    )


async def search_images(
    query: str,
    quantidade: int = 4,
) -> dict:
    """Busca fotos na internet para mostrar ao usuário na conversa.

    Use somente quando o usuário pedir fotos ou imagens (de um destino,
    atração, hotel etc.). As fotos aparecem automaticamente na conversa,
    abaixo da sua resposta; não escreva os links das imagens no texto.
    Prefira uma única busca por pedido.

    Args:
        query: o que buscar, de forma específica, por exemplo
            "Templo de Abu Simbel Egito".
        quantidade: quantas fotos mostrar; use exatamente o número que o
            usuário pediu (por exemplo, 2 para "duas imagens").
    """
    if not IMAGES_REQUESTED.get():
        return {
            "status": "erro",
            "mensagem": (
                "O usuário não pediu fotos nesta mensagem. Não busque "
                "imagens; responda normalmente."
            ),
        }

    budget = IMAGE_BUDGET.get()

    try:
        wanted = int(quantidade)
    except (TypeError, ValueError):
        wanted = 4

    limit = max(1, min(wanted, MAX_IMAGES))

    if budget is not None:
        if budget[0] <= 0:
            return {
                "status": "erro",
                "mensagem": (
                    "A quantidade de fotos pedida já foi atingida. Não "
                    "busque mais; responda ao usuário."
                ),
            }

        limit = min(limit, budget[0])

    clean_query = " ".join(str(query).split())

    if not clean_query:
        return {
            "status": "erro",
            "mensagem": "Informe o que buscar.",
        }

    api_key = os.getenv("TAVILY_API_KEY")

    if not api_key:
        raise RuntimeError(
            "A variável TAVILY_API_KEY não foi configurada."
        )

    # A chamada ao Tavily é bloqueante; roda fora do loop assíncrono.
    response = await asyncio.to_thread(
        TavilyClient(api_key=api_key).search,
        query=clean_query,
        search_depth="basic",
        topic="general",
        max_results=5,
        include_answer=False,
        include_raw_content=False,
        include_images=True,
        include_image_descriptions=True,
        timeout=30,
    )

    images = []
    seen_urls = set()

    for item in response.get("images", []):
        if isinstance(item, dict):
            url = str(item.get("url", "")).strip()
            description = str(item.get("description") or "").strip()
        else:
            url = str(item).strip()
            description = ""

        if url in seen_urls or not _is_usable_image_url(url):
            continue

        seen_urls.add(url)
        images.append(
            {
                "url": url,
                "description": description,
            }
        )

        if len(images) >= limit:
            break

    if budget is not None:
        budget[0] -= len(images)

    return {
        "status": "ok" if images else "sem_resultados",
        "query": clean_query,
        "images": images,
        "mensagem": (
            f"{len(images)} fotos encontradas. A interface já as exibe "
            "sozinha: não mencione isso, não escreva avisos entre colchetes "
            "e não liste os links. Escreva só uma frase sobre as fotos."
            if images
            else "Nenhuma foto encontrada. Informe isso ao usuário."
        ),
    }
