"""Decide se a base de conhecimento responde antes da IA.

- Similaridade >= AGORA_BASE_LIMIAR_DIRETO: responde direto com a resposta
  aprovada (sem custo de modelo). Só na 1ª pergunta da conversa: no meio de
  uma conversa a pergunta depende do contexto, então vira "contexto".
- Entre AGORA_BASE_LIMIAR_CONTEXTO e o limiar direto: a IA responde com a
  resposta aprovada como contexto interno.
- Abaixo: fluxo normal.
Qualquer falha (embedding, banco) segue o fluxo normal: a base nunca
impede uma resposta.
"""

from dataclasses import dataclass
import os
import threading
import time

from supabase import Client

from database.knowledge_entries import has_approved_entries, match_entries
from services.embeddings import embed_text, vector_literal

# Calibrados com text-embedding-3-small (768): mesma pergunta com outra
# grafia ~0,96; mesma dúvida com outras palavras ~0,57; assunto sem relação
# ~0,30. Trocar o modelo de embedding exige recalibrar.
DEFAULT_DIRECT_THRESHOLD = 0.92
DEFAULT_CONTEXT_THRESHOLD = 0.55

# Base vazia: não calcula embedding (economiza ~1 s por mensagem). A
# conferência vale para todos (as aprovadas são visíveis a todos) e é
# refeita a cada minuto, para uma aprovação nova passar a valer logo.
EMPTY_BASE_CHECK_SECONDS = 60

MODE_DIRECT = "direta"
MODE_CONTEXT = "contexto"

DIRECT_ANSWER_NOTE = (
    "\n\n_Resposta da base de conhecimento da Latitudes, aprovada pelo TI._"
)


@dataclass(frozen=True)
class KnowledgeMatch:
    entry_id: str
    question: str
    answer: str
    similarity: float
    mode: str


def _threshold(
    name: str,
    default: float,
) -> float:
    raw_value = os.getenv(name, "").strip().replace(",", ".")

    if not raw_value:
        return default

    try:
        value = float(raw_value)
    except ValueError:
        print(f"[CONFIG] {name} inválido; usando {default}.", flush=True)
        return default

    if not 0 < value <= 1:
        print(f"[CONFIG] {name} fora de 0–1; usando {default}.", flush=True)
        return default

    return value


def thresholds() -> tuple[float, float]:
    """(limiar direto, limiar de contexto)."""
    direct = _threshold("AGORA_BASE_LIMIAR_DIRETO", DEFAULT_DIRECT_THRESHOLD)
    context = _threshold("AGORA_BASE_LIMIAR_CONTEXTO", DEFAULT_CONTEXT_THRESHOLD)

    if context > direct:
        print(
            "[CONFIG] AGORA_BASE_LIMIAR_CONTEXTO maior que o direto; "
            "usando os padrões.",
            flush=True,
        )
        return DEFAULT_DIRECT_THRESHOLD, DEFAULT_CONTEXT_THRESHOLD

    return direct, context


def classify(
    similarity: float,
    first_question: bool,
) -> str | None:
    direct, context = thresholds()

    if similarity >= direct:
        return MODE_DIRECT if first_question else MODE_CONTEXT

    if similarity >= context:
        return MODE_CONTEXT

    return None


_base_state_lock = threading.Lock()
_base_state: tuple[float, bool] | None = None


def _base_has_entries(client: Client) -> bool:
    global _base_state

    with _base_state_lock:
        if (
            _base_state is not None
            and time.monotonic() - _base_state[0] < EMPTY_BASE_CHECK_SECONDS
        ):
            return _base_state[1]

    has_entries = has_approved_entries(client)

    with _base_state_lock:
        _base_state = (time.monotonic(), has_entries)

    return has_entries


def forget_base_state() -> None:
    """Chamada quando o TI muda a base: a próxima pergunta confere de novo."""
    global _base_state

    with _base_state_lock:
        _base_state = None


def find_knowledge_match(
    client: Client,
    question: str,
    first_question: bool,
) -> KnowledgeMatch | None:
    if not question.strip():
        return None

    try:
        if not _base_has_entries(client):
            return None

        embedding = embed_text(question)
        matches = match_entries(
            client=client,
            embedding=vector_literal(embedding),
            match_count=1,
        )
    except Exception as error:
        print(
            f"[BASE] Busca indisponível ({type(error).__name__}); "
            "seguindo sem a base.",
            flush=True,
        )
        return None

    if not matches:
        return None

    best = matches[0]
    similarity = float(best.get("similarity") or 0)
    mode = classify(
        similarity=similarity,
        first_question=first_question,
    )

    print(
        f"[BASE] similaridade={similarity:.3f} modo={mode or 'normal'}",
        flush=True,
    )

    if mode is None:
        return None

    return KnowledgeMatch(
        entry_id=str(best["id"]),
        question=str(best.get("question", "")),
        answer=str(best.get("answer", "")),
        similarity=similarity,
        mode=mode,
    )


def build_context_message(match: KnowledgeMatch) -> dict:
    """Mensagem de contexto interno para o agente (vira "CONTEXTO INTERNO")."""
    return {
        "role": "system",
        "content": (
            "Há uma resposta APROVADA pelo TI da Latitudes na base de "
            "conhecimento para uma pergunta parecida. Use-a como referência "
            "principal e adapte ao que a pessoa perguntou agora; não diga que "
            "existe uma base nem copie se não couber.\n\n"
            f"PERGUNTA APROVADA: {match.question}\n\n"
            f"RESPOSTA APROVADA:\n{match.answer}"
        ),
        "attachments": [],
    }
