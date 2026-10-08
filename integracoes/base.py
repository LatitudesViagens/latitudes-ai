"""Interface comum dos conectores. Um sistema novo (V2: Stur, RD Station
Marketing) entra implementando esta interface e se registrando em SISTEMAS,
sem mudar os existentes."""

from typing import Protocol


class Conector(Protocol):
    nome: str

    def testar_conexao(self) -> None:
        """Faz a consulta mais leve possível; levanta IntegracaoError se falhar."""


def pode_consultar(
    usuario_id: str | None,
    sistema: str,
) -> bool:
    """Permissões por sistema. V1: todos os usuários da ÁGORA consultam RD CRM
    e Envision (regra 5). Na V2, grupos de acesso do painel do TI."""
    return sistema in {"RD Station CRM", "Envision"}
