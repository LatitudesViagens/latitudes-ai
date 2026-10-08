"""Quem está perguntando no turno atual, para as ferramentas do agente.

O chat_service publica aqui o usuário e a conversa antes de chamar a IA. A
ferramenta lê daqui (nunca de argumentos que a IA possa preencher) para
anotar as consultas e, na V2, conferir permissões.
"""

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class TurnoAtual:
    user_id: str
    conversation_id: str
    # Consultas feitas pelas ferramentas neste turno. As ferramentas rodam em
    # outra thread e só ANOTAM aqui; quem grava no banco é o turno, na própria
    # thread (a conexão do Supabase não pode ser usada por duas threads).
    consultas_cliente: list[dict] = field(default_factory=list)


TURNO_ATUAL: ContextVar[TurnoAtual | None] = ContextVar(
    "agora_turno_atual",
    default=None,
)
