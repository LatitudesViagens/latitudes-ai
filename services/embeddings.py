"""Embeddings (vetores de significado) para a base de conhecimento.

Pelo OpenRouter, com a mesma chave do agente. O OpenRouter não devolve o
custo dos embeddings, então ele é calculado pelos tokens e pelo preço do
modelo. Trocar de modelo exige recalcular os embeddings já aprovados
(todos precisam vir do mesmo modelo para serem comparáveis).
"""

import os
from pathlib import Path

from dotenv import load_dotenv
import litellm

from services.custos import note_usage_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "latitudes_agent" / ".env"

load_dotenv(dotenv_path=ENV_FILE)

EMBEDDING_MODEL = os.getenv(
    "AGORA_MODELO_EMBEDDING",
    "openrouter/openai/text-embedding-3-small",
)
# Precisa bater com a coluna knowledge_entries.embedding (migração 009).
EMBEDDING_DIMENSIONS = 768
EMBEDDING_TIMEOUT_SECONDS = 8
MAX_EMBEDDING_CHARACTERS = 4000

# US$ por 1 milhão de tokens de entrada.
EMBEDDING_PRICES_PER_MILLION = {
    "openrouter/openai/text-embedding-3-small": 0.02,
    "openrouter/google/gemini-embedding-001": 0.15,
}


def embed_text(
    text: str,
    purpose: str = "embedding",
) -> list[float]:
    """Vetor de 768 números que representa o significado do texto."""
    response = litellm.embedding(
        model=EMBEDDING_MODEL,
        input=[text.strip()[:MAX_EMBEDDING_CHARACTERS]],
        dimensions=EMBEDDING_DIMENSIONS,
        timeout=EMBEDDING_TIMEOUT_SECONDS,
        num_retries=0,
    )
    vector = list(response.data[0]["embedding"])

    if len(vector) != EMBEDDING_DIMENSIONS:
        raise RuntimeError(
            f"Embedding com {len(vector)} dimensões; esperado "
            f"{EMBEDDING_DIMENSIONS}."
        )

    prompt_tokens = getattr(response.usage, "prompt_tokens", None)
    price = EMBEDDING_PRICES_PER_MILLION.get(EMBEDDING_MODEL)
    note_usage_values(
        purpose=purpose,
        model=EMBEDDING_MODEL,
        prompt_tokens=prompt_tokens,
        completion_tokens=0,
        cost_usd=(
            prompt_tokens * price / 1_000_000
            if prompt_tokens is not None and price is not None
            else None
        ),
    )

    return vector


def vector_literal(vector: list[float]) -> str:
    """Formato aceito pelo pgvector ('[0.1,0.2,...]')."""
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"
