"""Cliente do LiteLLM usado pelo agente para guardar o custo de cada chamada.

O ADK converte a resposta do LiteLLM e descarta o custo informado pelo
OpenRouter (usage.cost). Este cliente lê o custo antes da conversão.
"""

from typing import Any

from google.adk.models.lite_llm import LiteLLMClient

from services.custos import add_response_cost


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

        # Com streaming a resposta é um iterador sem custo; o agente usa
        # StreamingMode.NONE, então aqui chega a resposta completa.
        add_response_cost(response)

        return response
