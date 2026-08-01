"""Portas — as interfaces que a camada de infraestrutura implementa.

Este arquivo é a fronteira da arquitetura hexagonal. O núcleo (``domain/``) e os
casos de uso (``app/``) dependem **apenas** destes ``Protocol``; nunca de Redis,
DynamoDB ou de qualquer biblioteca de I/O.

O ganho não é abstrato: como existe uma implementação em memória de cada porta
(``infra/memory/``), a suíte de testes sobe **três nós simulados no mesmo
processo** e verifica a ordenação total sem Redis, sem AWS e sem Docker. É o que
torna o teste que prova o requisito central do projeto executável na máquina de
qualquer avaliador.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from salaviva.domain.models import Member, MessageEnvelope, NodeInfo, RoomSummary

__all__ = [
    "IdempotencyStore",
    "MessageBus",
    "MessageHandler",
    "MessageRepository",
    "NodeRegistry",
    "PresenceStore",
    "Sequencer",
]

MessageHandler = Callable[[str, MessageEnvelope], Awaitable[None]]
"""Callback invocado quando chega uma mensagem de um tópico: ``(room_id, env)``."""


@runtime_checkable
class MessageBus(Protocol):
    """Canal de comunicação indireta publish-subscribe entre nós.

    É a peça que concretiza o critério EC2 ("comunicação indireta via
    filas/tópicos"). Note que nenhum método recebe a identidade de um nó
    destinatário: quem publica desconhece quem consome, e vice-versa. Esse
    desacoplamento no espaço é o que permite ao Auto Scaling adicionar ou
    remover nós sem reconfigurar nenhum outro.
    """

    async def start(self) -> None:
        """Conecta ao broker e inicia o laço de recepção."""
        ...

    async def stop(self) -> None:
        """Encerra assinaturas e fecha a conexão."""
        ...

    def on_message(self, handler: MessageHandler) -> None:
        """Registra o callback de entrega."""
        ...

    async def publish(self, room_id: str, envelope: MessageEnvelope) -> None:
        """Difunde ``envelope`` no tópico da sala.

        O nó emissor **também** recebe a própria mensagem de volta por este
        canal — ver ADR-004, que explica por que o atalho local foi rejeitado.
        """
        ...

    async def subscribe(self, room_id: str) -> None:
        """Assina o tópico da sala (idempotente)."""
        ...

    async def unsubscribe(self, room_id: str) -> None:
        """Cancela a assinatura (idempotente)."""
        ...

    async def healthy(self) -> bool:
        """``False`` faz ``/readyz`` reprovar e o ALB retirar o nó do pool."""
        ...


@runtime_checkable
class Sequencer(Protocol):
    """Atribui números de sequência monotônicos por sala.

    É o árbitro da ordem total (ADR-003). A implementação de produção é um
    ``INCR`` atômico no Redis — uma única operação, sem lock e sem retry.
    """

    async def next_seq(self, room_id: str) -> int:
        """Reserva e devolve o próximo ``seq`` da sala. Atômico."""
        ...

    async def current(self, room_id: str) -> int:
        """Último ``seq`` atribuído, sem consumir um novo."""
        ...


@runtime_checkable
class MessageRepository(Protocol):
    """Histórico durável de mensagens."""

    async def save(self, envelope: MessageEnvelope) -> None:
        """Persiste a mensagem.

        Chamado fora do caminho crítico de entrega (ADR-008): uma falha aqui
        degrada o replay de histórico, jamais a entrega em tempo real.
        """
        ...

    async def backlog(
        self, room_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[MessageEnvelope]:
        """Mensagens da sala com ``seq > after_seq``, em ordem crescente.

        É o mecanismo de recuperação sem perda: um cliente que perdeu o nó
        reconecta informando o último ``seq`` visto e recebe exatamente a
        lacuna (FR-8).
        """
        ...

    async def healthy(self) -> bool: ...


@runtime_checkable
class PresenceStore(Protocol):
    """Quem está online em cada sala.

    Consistência eventual assumida conscientemente: um membro fantasma pode
    permanecer visível por até um ciclo de sweeper (15 s). Pagar coordenação
    forte por presença não se justifica — é o lado AP do trade-off do Teorema
    CAP neste sistema, enquanto a ordenação fica do lado CP.
    """

    async def join(self, room_id: str, member: Member) -> None: ...

    async def leave(self, room_id: str, session_id: str) -> None: ...

    async def heartbeat(self, room_id: str, session_id: str) -> None:
        """Renova o TTL implícito do membro."""
        ...

    async def members(self, room_id: str) -> list[Member]: ...

    async def rooms(self) -> list[RoomSummary]: ...

    async def sweep(self, max_age_seconds: int = 15) -> int:
        """Remove membros sem heartbeat recente. Devolve quantos foram removidos.

        É o que faz os usuários de um nó morto desaparecerem das listas sem que
        ninguém precise notificar a morte — nenhum nó detecta a falha de outro,
        e ainda assim o sistema converge.
        """
        ...


@runtime_checkable
class NodeRegistry(Protocol):
    """Registro dos nós vivos do cluster.

    Existe para observabilidade, não para roteamento: nenhum nó consulta este
    registro para decidir para onde enviar algo. Ele alimenta o painel
    ``/dashboard``, onde a plateia vê, ao vivo, o nó derrubado sumir e o
    substituto criado pelo Auto Scaling aparecer.
    """

    async def heartbeat(self, info: NodeInfo) -> None: ...

    async def alive(self, max_age_seconds: int = 15) -> list[NodeInfo]: ...

    async def sweep(self, max_age_seconds: int = 15) -> int: ...


@runtime_checkable
class IdempotencyStore(Protocol):
    """Deduplicação de envios por ``client_msg_id`` (FR-9)."""

    async def claim(self, key: str, value: str, ttl_seconds: int = 300) -> str | None:
        """Reivindica ``key`` atomicamente (``SET NX``).

        Devolve ``None`` se a reivindicação foi bem-sucedida (primeira vez), ou
        o valor já armazenado se a chave existia — permitindo devolver ao
        cliente o mesmo ``ack`` do envio original, em vez de um novo ``seq``.

        A atomicidade importa: um ``GET`` seguido de ``SET`` abriria uma janela
        em que dois reenvios simultâneos passariam ambos.
        """
        ...

    async def record(self, key: str, value: str, ttl_seconds: int = 300) -> None:
        """Sobrescreve ``key`` (``SET`` simples).

        Usado para substituir o marcador provisório da reivindicação pelo
        resultado real (``"{seq}:{lamport}"``) assim que o ``seq`` é conhecido.
        """
        ...
