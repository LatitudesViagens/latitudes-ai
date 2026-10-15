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
import json
import math
import threading
import time

from supabase import Client

from database.knowledge_entries import has_approved_entries, list_approved_embeddings, match_entries
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
    "\n\n_Resposta da base de conhecimento da Latitudes._"
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


def base_known_empty() -> bool:
    """True só se a última conferência (até 60 s atrás) achou a base vazia:
    aí nem vale calcular o embedding da pergunta."""
    with _base_state_lock:
        return (
            _base_state is not None
            and time.monotonic() - _base_state[0] < EMPTY_BASE_CHECK_SECONDS
            and not _base_state[1]
        )


def prefetch_question_embedding(question: str) -> tuple[list[float] | None, list]:
    """Embedding da pergunta calculado já no início da tarefa, em paralelo com
    a conexão e a leitura da conversa (antes era calculado só depois delas).
    Devolve (embedding ou None, usos para o registro de custo). Nunca lança
    erro: sem embedding, a consulta à base calcula de novo."""
    from services.custos import collect_usage

    try:
        with collect_usage() as usages:
            embedding = embed_text(question)

        return embedding, list(usages)
    except Exception as error:
        print(f"[BASE] Cálculo antecipado falhou ({type(error).__name__}).", flush=True)
        return None, []


def forget_base_state() -> None:
    """Chamada quando o TI muda a base: a próxima pergunta confere de novo."""
    global _base_state, _entries_cache

    with _base_state_lock:
        _base_state = None
        _entries_cache = None


# Respostas aprovadas na memória do servidor: lidas do banco no máximo a cada
# 10 minutos. Toda mudança do TI (incluir, aprovar, editar, retirar) já limpa a
# lista na hora (admin/base_conhecimento.py → forget_base_state). Antes, cada pergunta fazia
# uma consulta ao banco para comparar (0,6 a 1,7 s medidos em 13/10/2026).
_entries_cache: tuple[float, list[tuple[str, str, str, list[float], float]]] | None = None
ENTRIES_CACHE_SECONDS = 600


def _parse_vector(value) -> list[float] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None

    if not isinstance(value, list) or not value:
        return None

    try:
        return [float(number) for number in value]
    except (TypeError, ValueError):
        return None


def _approved_entries(client: Client) -> list[tuple[str, str, str, list[float], float]]:
    """(id, pergunta, resposta, vetor, norma) das respostas aprovadas."""
    global _entries_cache, _base_state

    with _base_state_lock:
        if _entries_cache is not None and time.monotonic() - _entries_cache[0] < ENTRIES_CACHE_SECONDS:
            return _entries_cache[1]

    entries = []

    for row in list_approved_embeddings(client):
        vector = _parse_vector(row.get("embedding"))

        if vector is None:
            continue

        norm = math.sqrt(sum(number * number for number in vector))

        if norm > 0:
            entries.append((str(row["id"]), str(row.get("question", "")), str(row.get("answer", "")), vector, norm))

    with _base_state_lock:
        _entries_cache = (time.monotonic(), entries)
        _base_state = (time.monotonic(), bool(entries))

    return entries


def _best_match(
    entries: list[tuple[str, str, str, list[float], float]],
    embedding: list[float],
) -> tuple[tuple, float] | None:
    """Mesma conta do banco (1 - distância de cosseno do pgvector)."""
    query_norm = math.sqrt(sum(number * number for number in embedding))

    if query_norm == 0:
        return None

    best = None

    for entry in entries:
        vector, norm = entry[3], entry[4]

        if len(vector) != len(embedding):
            continue

        similarity = sum(a * b for a, b in zip(vector, embedding)) / (norm * query_norm)

        if best is None or similarity > best[1]:
            best = (entry, similarity)

    return best


def find_knowledge_match(
    client: Client,
    question: str,
    first_question: bool,
    embedding: list[float] | None = None,
) -> KnowledgeMatch | None:
    if not question.strip():
        return None

    try:
        started = time.perf_counter()
        entries = _approved_entries(client)
        loaded = time.perf_counter()

        if not entries:
            return None

        if embedding is None:
            embedding = embed_text(question)

        embedded = time.perf_counter()
        found = _best_match(entries, embedding)
        print(
            f"[BASE] tempos respostas_aprovadas={loaded - started:.2f}s "
            f"embedding={embedded - loaded:.2f}s "
            f"comparacao={time.perf_counter() - embedded:.3f}s ({len(entries)} respostas)",
            flush=True,
        )
    except Exception as error:
        print(
            f"[BASE] Busca indisponível ({type(error).__name__}); "
            "seguindo sem a base.",
            flush=True,
        )
        return None

    if found is None:
        return None

    entry, similarity = found
    best = {"id": entry[0], "question": entry[1], "answer": entry[2]}
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
