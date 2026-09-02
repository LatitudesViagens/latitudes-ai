import re
import unicodedata

from supabase import Client


def normalize_keyword(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFKD",
        value.strip().lower(),
    )

    without_accents = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )

    only_words = re.sub(
        r"[^a-z0-9\s'-]",
        " ",
        without_accents,
    )

    return " ".join(only_words.split())


def _clean_text_list(values: list[str] | None) -> list[str]:
    if not values:
        return []

    cleaned_values = []
    seen_values = set()

    for value in values:
        clean_value = " ".join(str(value).split()).strip()

        if not clean_value:
            continue

        comparison_value = clean_value.casefold()

        if comparison_value in seen_values:
            continue

        cleaned_values.append(clean_value)
        seen_values.add(comparison_value)

    return cleaned_values


def _build_keywords(
    destination: str,
    interests: list[str],
    additional_keywords: list[str] | None,
) -> list[str]:
    raw_keywords = [
        destination,
        *interests,
        *(additional_keywords or []),
    ]

    normalized_keywords = []
    seen_keywords = set()

    for keyword in raw_keywords:
        normalized_keyword = normalize_keyword(keyword)

        if (
            not normalized_keyword
            or normalized_keyword in seen_keywords
        ):
            continue

        normalized_keywords.append(normalized_keyword)
        seen_keywords.add(normalized_keyword)

    return normalized_keywords


def publish_itinerary(
    client: Client,
    user_id: str,
    conversation_id: str,
    message_id: str,
    title: str,
    destination: str,
    content: str,
    sources: list[dict] | None = None,
    duration_days: int | None = None,
    traveler_profile: str | None = None,
    interests: list[str] | None = None,
    budget_range: str | None = None,
    keywords: list[str] | None = None,
) -> dict:
    clean_title = " ".join(title.split()).strip()
    clean_destination = " ".join(destination.split()).strip()
    clean_content = content.strip()

    if not clean_title:
        raise ValueError("Informe o título do roteiro.")

    if not clean_destination:
        raise ValueError("Informe o destino do roteiro.")

    if not clean_content:
        raise ValueError(
            "A versão final do roteiro não pode estar vazia."
        )

    if (
        duration_days is not None
        and not 1 <= duration_days <= 365
    ):
        raise ValueError(
            "A duração deve estar entre 1 e 365 dias."
        )

    clean_interests = _clean_text_list(interests)
    clean_keywords = _build_keywords(
        destination=clean_destination,
        interests=clean_interests,
        additional_keywords=keywords,
    )

    clean_traveler_profile = (
        " ".join(traveler_profile.split()).strip()
        if traveler_profile
        else None
    )

    clean_budget_range = (
        " ".join(budget_range.split()).strip()
        if budget_range
        else None
    )

    itinerary_data = {
        "source_conversation_id": str(conversation_id),
        "source_message_id": str(message_id),
        "created_by": str(user_id),
        "title": clean_title,
        "destination": clean_destination,
        "duration_days": duration_days,
        "traveler_profile": clean_traveler_profile,
        "interests": clean_interests,
        "budget_range": clean_budget_range,
        "keywords": clean_keywords,
        "content": clean_content,
        "sources": sources or [],
    }

    response = (
        client.table("shared_itineraries")
        .upsert(
            itinerary_data,
            on_conflict="source_conversation_id",
        )
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Não foi possível publicar o roteiro."
        )

    return response.data[0]


def find_shared_itineraries(
    client: Client,
    destination: str,
    limit: int = 5,
) -> list[dict]:
    clean_destination = " ".join(destination.split()).strip()

    if not clean_destination:
        return []

    if not 1 <= limit <= 20:
        raise ValueError(
            "O limite deve estar entre 1 e 20."
        )

    columns = (
        "id,"
        "source_conversation_id,"
        "title,"
        "destination,"
        "duration_days,"
        "traveler_profile,"
        "interests,"
        "budget_range,"
        "keywords,"
        "content,"
        "sources,"
        "published_at,"
        "updated_at"
    )

    response = (
        client.table("shared_itineraries")
        .select(columns)
        .ilike(
            "destination",
            f"%{clean_destination}%",
        )
        .order("updated_at", desc=True)
        .limit(limit)
        .execute()
    )

    if response.data:
        return response.data

    normalized_destination = normalize_keyword(
        clean_destination
    )

    if not normalized_destination:
        return []

    fallback_response = (
        client.table("shared_itineraries")
        .select(columns)
        .contains(
            "keywords",
            [normalized_destination],
        )
        .order("updated_at", desc=True)
        .limit(limit)
        .execute()
    )

    return fallback_response.data


def remove_shared_itinerary(
    client: Client,
    conversation_id: str,
) -> None:
    (
        client.table("shared_itineraries")
        .delete()
        .eq(
            "source_conversation_id",
            str(conversation_id),
        )
        .execute()
    )


def get_shared_itinerary(
    client: Client,
    itinerary_id: str,
) -> dict | None:
    response = (
        client.table("shared_itineraries")
        .select("*")
        .eq("id", str(itinerary_id))
        .limit(1)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]


def _contains_normalized_phrase(
    normalized_text: str,
    normalized_phrase: str,
) -> bool:
    if not normalized_phrase:
        return False

    pattern = (
        rf"(?<![a-z0-9])"
        rf"{re.escape(normalized_phrase)}"
        rf"(?![a-z0-9])"
    )

    return re.search(
        pattern,
        normalized_text,
    ) is not None


def find_shared_itineraries_for_message(
    client: Client,
    message: str,
    limit: int = 5,
) -> list[dict]:
    normalized_message = normalize_keyword(message)

    if not normalized_message:
        return []

    if not 1 <= limit <= 20:
        raise ValueError(
            "O limite deve estar entre 1 e 20."
        )

    columns = (
        "id,"
        "source_conversation_id,"
        "title,"
        "destination,"
        "duration_days,"
        "traveler_profile,"
        "interests,"
        "budget_range,"
        "keywords,"
        "content,"
        "sources,"
        "published_at,"
        "updated_at"
    )

    response = (
        client.table("shared_itineraries")
        .select(columns)
        .order("updated_at", desc=True)
        .limit(100)
        .execute()
    )

    scored_matches = []

    for itinerary in response.data:
        normalized_destination = normalize_keyword(
            itinerary.get("destination", "")
        )

        if not _contains_normalized_phrase(
            normalized_text=normalized_message,
            normalized_phrase=normalized_destination,
        ):
            continue

        matched_keywords = 0

        for keyword in itinerary.get("keywords", []):
            if _contains_normalized_phrase(
                normalized_text=normalized_message,
                normalized_phrase=normalize_keyword(keyword),
            ):
                matched_keywords += 1

        scored_matches.append(
            (
                100 + matched_keywords,
                itinerary,
            )
        )

    scored_matches.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return [
        itinerary
        for _, itinerary in scored_matches[:limit]
    ]    
