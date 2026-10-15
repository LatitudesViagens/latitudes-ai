"""Cliente do LiteLLM usado pelo agente para guardar o custo de cada chamada.

O ADK converte a resposta do LiteLLM e descarta o custo informado pelo
OpenRouter (usage.cost). Este cliente lê o custo antes da conversão.
"""

from typing import Any

from google.adk.models.lite_llm import LiteLLMClient

from services.custos import (
    add_estimated_cost,
    add_response_cost,
    estimate_openrouter_cost,
    response_cost_usd,
)


class CostTrackingLiteLLMClient(LiteLLMClient):
    async def acompletion(
        self,
        model: Any,
        messages: Any,
        tools: Any,
        **kwargs: Any,
    ):
        response = await super().acompletion(
            model=model,
            messages=messages,
            tools=tools,
            **kwargs,
        )

        # Em pedaços (resposta aparecendo enquanto é escrita), o custo vem só no
        # último pedaço, com o resumo de uso (stream_options include_usage).
        if hasattr(response, "__aiter__") and not hasattr(response, "choices"):
            return _track_stream_cost(
                stream=response,
                model=str(model),
            )

        add_response_cost(response)

        return response


async def _track_stream_cost(
    stream,
    model: str,
):
    """Repassa os pedaços e soma o custo uma vez no fim. Se o provedor não
    informar o custo (o LiteLLM não repassa nesse modo), calcula pelos tokens
    do último pedaço com a tabela de preços do OpenRouter."""
    priced_chunk = None
    usage_chunk = None

    try:
        async for chunk in stream:
            if response_cost_usd(chunk) is not None:
                priced_chunk = chunk

            if getattr(chunk, "usage", None) is not None:
                usage_chunk = chunk

            yield chunk
    finally:
        if priced_chunk is not None:
            add_response_cost(priced_chunk)
        elif usage_chunk is not None:
            add_estimated_cost(
                estimate_openrouter_cost(
                    model=model,
                    prompt_tokens=getattr(usage_chunk.usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage_chunk.usage, "completion_tokens", None),
                )
            )
