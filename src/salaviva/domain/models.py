"""Modelos de domínio.

O :class:`MessageEnvelope` é o formato canônico que trafega por todo o sistema:
é o mesmo objeto publicado no tópico Redis, gravado no DynamoDB e entregue ao
cliente pelo WebSocket. Ter um único formato — em vez de um DTO por camada —
elimina a classe de bug em que os campos de ordenação (``seq``, ``lamport``,
``vector_clock``) se perdem em uma tradução intermediária.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Member", "MessageEnvelope", "NodeInfo", "RoomSummary", "utc_now_iso"]

MAX_CONTENT_LENGTH = 4096
"""Limite de conteúdo por mensagem, em caracteres (NFR de segurança)."""


def utc_now_iso() -> str:
    """Timestamp ISO-8601 em UTC.

    Usado **apenas para exibição**. Nunca para ordenar: relógios de instâncias
    EC2 divergem mesmo sob NTP, e ordenar por tempo físico produziria a inversão
    causal que o relógio lógico existe para evitar (ADR-003).
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class MessageEnvelope(BaseModel):
    """Uma mensagem de chat com todos os metadados de ordenação distribuída."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """Identidade da mensagem atribuída pelo servidor."""

    client_msg_id: str
    """Identidade atribuída pelo cliente. Base da idempotência (FR-9)."""

    room_id: str
    sender: str
    session_id: str
    content: str = Field(max_length=MAX_CONTENT_LENGTH)

    seq: int = Field(ge=0)
    """Número de sequência da sala. **Define a ordem total de entrega.**

    Obtido de um ``INCR`` atômico no Redis, portanto único e monotônico dentro
    da sala. É por este campo — e apenas por ele — que os clientes ordenam.
    """

    lamport: int = Field(ge=0)
    """Carimbo do relógio lógico de Lamport do nó de origem.

    Estabelece *happened-before*. Não define ordem de entrega (ver ADR-005).
    """

    vector_clock: dict[str, int] = Field(default_factory=dict)
    """Carimbo vetorial. Permite detectar concorrência entre mensagens."""

    node_id: str
    """Nó que originou a mensagem. Evidência de distribuição na demonstração."""

    ts: str = Field(default_factory=utc_now_iso)
    """Timestamp físico — informativo, nunca usado para ordenar."""

    def to_wire(self) -> dict:
        """Serializa para o protocolo WebSocket (envelope + discriminador)."""
        return {"type": "message", **self.model_dump()}

    def sort_key(self) -> tuple[int, int, str]:
        """Chave de ordenação determinística.

        ``seq`` já é único por sala, então basta ele na prática. Os dois campos
        seguintes existem para tornar a ordenação total mesmo em cenários de
        teste que constroem envelopes sem passar pelo sequenciador.
        """
        return (self.seq, self.lamport, self.node_id)


class Member(BaseModel):
    """Participante presente em uma sala."""

    model_config = ConfigDict(frozen=True)

    user: str
    session_id: str
    node_id: str
    joined_at: str = Field(default_factory=utc_now_iso)


class RoomSummary(BaseModel):
    """Visão resumida de uma sala, para a listagem de salas."""

    room_id: str
    member_count: int
    last_seq: int = 0


class NodeInfo(BaseModel):
    """Estado publicado de um nó do cluster.

    Alimenta o painel ``/dashboard``. Um nó derrubado some do painel em até um
    ciclo de sweeper (15 s) — que é a evidência visual usada na demonstração de
    tolerância a falhas (FR-10).
    """

    node_id: str
    address: str = ""
    connections: int = 0
    rooms: int = 0
    messages_published: int = 0
    messages_delivered: int = 0
    lamport: int = 0
    uptime_seconds: float = 0.0
    last_heartbeat: str = Field(default_factory=utc_now_iso)
