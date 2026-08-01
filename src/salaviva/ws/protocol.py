"""Protocolo WebSocket cliente ↔ servidor.

Todo frame é um objeto JSON com um campo discriminador ``type``. Os comandos do
cliente formam uma união discriminada validada pelo Pydantic v2, o que faz um
payload malformado ser rejeitado **na borda**, antes de tocar qualquer estado —
em vez de estourar como exceção no meio do caso de uso.

A documentação legível deste protocolo está em ``docs/protocolo.md``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from salaviva.domain.models import MAX_CONTENT_LENGTH, Member, MessageEnvelope

__all__ = [
    "Ack",
    "ClientCommand",
    "ErrorFrame",
    "JoinCommand",
    "JoinedFrame",
    "LeaveCommand",
    "PingCommand",
    "PresenceUpdate",
    "ResyncCommand",
    "SendCommand",
    "TypingCommand",
    "WelcomeFrame",
    "parse_client_command",
]

# ---------------------------------------------------------------------------
# Cliente → Servidor
# ---------------------------------------------------------------------------


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JoinCommand(_Command):
    """Entra em uma sala (cria se não existir).

    ``last_seq`` é o mecanismo de recuperação sem perda: o cliente informa o
    último número de sequência que já viu e recebe de volta exatamente o que
    perdeu. Em uma primeira conexão vale 0; após uma queda de nó, vale o último
    ``seq`` renderizado.
    """

    type: Literal["join"]
    room: str = Field(min_length=1, max_length=64, pattern=r"^[\w\-.]+$")
    last_seq: int = Field(default=0, ge=0)


class LeaveCommand(_Command):
    type: Literal["leave"]
    room: str = Field(min_length=1, max_length=64)


class SendCommand(_Command):
    type: Literal["send"]
    room: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)
    client_msg_id: str = Field(min_length=1, max_length=64)
    """Identidade gerada pelo cliente. Reenviar com o mesmo valor é seguro:
    o servidor devolve o ``ack`` original em vez de criar uma nova mensagem."""


class ResyncCommand(_Command):
    """Pede o backlog a partir de ``after_seq``.

    Emitido pelo cliente quando a fila de hold-back detecta uma lacuna que não
    se fecha — o caso em que o Pub/Sub *at-most-once* perdeu uma mensagem para
    aquele nó específico.
    """

    type: Literal["resync"]
    room: str = Field(min_length=1, max_length=64)
    after_seq: int = Field(default=0, ge=0)


class TypingCommand(_Command):
    type: Literal["typing"]
    room: str = Field(min_length=1, max_length=64)


class PingCommand(_Command):
    type: Literal["ping"]


ClientCommand = Annotated[
    JoinCommand | LeaveCommand | SendCommand | ResyncCommand | TypingCommand | PingCommand,
    Field(discriminator="type"),
]

_command_adapter: TypeAdapter[ClientCommand] = TypeAdapter(ClientCommand)


def parse_client_command(raw: str | bytes) -> ClientCommand:
    """Valida um frame recebido. Levanta ``ValidationError`` se inválido."""
    return _command_adapter.validate_json(raw)


# ---------------------------------------------------------------------------
# Servidor → Cliente
# ---------------------------------------------------------------------------


class WelcomeFrame(BaseModel):
    """Primeiro frame após o handshake.

    Carrega ``node_id`` para que o cliente possa exibir a qual instância está
    conectado — é o que torna visível, na demonstração, a migração dos clientes
    quando um nó é derrubado.
    """

    type: Literal["welcome"] = "welcome"
    node_id: str
    session_id: str
    user: str
    server_time: str


class JoinedFrame(BaseModel):
    type: Literal["joined"] = "joined"
    room: str
    members: list[Member]
    backlog: list[MessageEnvelope]
    last_seq: int
    node_id: str


class LeftFrame(BaseModel):
    type: Literal["left"] = "left"
    room: str


class Ack(BaseModel):
    """Confirmação de envio, devolvida ao remetente imediatamente.

    Chega antes do eco da mensagem pelo Pub/Sub. É o que permite à UI marcar a
    mensagem como enviada sem esperar o round-trip ao broker, tornando o custo
    de latência do ADR-004 imperceptível.
    """

    type: Literal["ack"] = "ack"
    client_msg_id: str
    room: str
    seq: int
    lamport: int
    duplicate: bool = False


class PresenceUpdate(BaseModel):
    type: Literal["presence_update"] = "presence_update"
    room: str
    event: Literal["join", "leave"]
    user: str
    members: list[Member]


class TypingFrame(BaseModel):
    type: Literal["typing"] = "typing"
    room: str
    user: str


class PongFrame(BaseModel):
    type: Literal["pong"] = "pong"
    server_time: str
    node_id: str


class ErrorFrame(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str
    detail: dict[str, Any] | None = None
