"""Implementações em memória de todas as portas.

Servem a dois propósitos:

1. **Testes.** Permitem montar *N nós no mesmo processo Python*, compartilhando
   um :class:`InMemoryBroker`, e verificar propriedades distribuídas — ordem
   total idêntica entre nós, ausência de perda, detecção de concorrência — sem
   Redis, sem AWS e sem Docker. É o que torna a prova do requisito central
   executável na máquina de qualquer avaliador com um único ``pytest``.

2. **Modo standalone.** Um único nó sobe sem nenhuma dependência externa
   (``SALAVIVA_REDIS_URL=memory://``), útil para desenvolvimento e como plano B
   caso a rede falhe no dia da apresentação.

O broker suporta injeção deliberada de **reordenação** e **perda**, o que
permite testar o caminho de recuperação em vez de apenas o caminho feliz.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict

from salaviva.domain.models import Member, MessageEnvelope, NodeInfo, RoomSummary
from salaviva.ports import MessageHandler

__all__ = [
    "InMemoryBroker",
    "InMemoryBus",
    "InMemoryIdempotencyStore",
    "InMemoryMessageRepository",
    "InMemoryNodeRegistry",
    "InMemoryPresenceStore",
    "InMemorySequencer",
]


class InMemoryBroker:
    """Simula o Redis: mantém tópicos, contadores, presença e registro de nós.

    Uma instância representa *um cluster*. Vários :class:`InMemoryBus` ligados ao
    mesmo broker representam vários nós do mesmo cluster.
    """

    def __init__(self, *, reorder_probability: float = 0.0, drop_probability: float = 0.0) -> None:
        self._topics: dict[str, list[InMemoryBus]] = defaultdict(list)
        self._seq: dict[str, int] = defaultdict(int)
        self._presence: dict[str, dict[str, tuple[Member, float]]] = defaultdict(dict)
        self._nodes: dict[str, tuple[NodeInfo, float]] = {}
        self._idempotency: dict[str, tuple[str, float]] = {}
        self.reorder_probability = reorder_probability
        """Probabilidade de atrasar uma entrega, provocando chegada fora de ordem."""
        self.drop_probability = drop_probability
        """Probabilidade de descartar uma entrega, simulando o caráter
        *at-most-once* do Redis Pub/Sub (ADR-002)."""
        self.published_count = 0

    # -- Pub/Sub -----------------------------------------------------------

    def _register(self, bus: InMemoryBus, room_id: str) -> None:
        subs = self._topics[room_id]
        if bus not in subs:
            subs.append(bus)

    def _unregister(self, bus: InMemoryBus, room_id: str) -> None:
        if bus in self._topics.get(room_id, []):
            self._topics[room_id].remove(bus)

    def _detach(self, bus: InMemoryBus) -> None:
        """Remove o nó de todos os tópicos — simula a morte de uma instância."""
        for subs in self._topics.values():
            if bus in subs:
                subs.remove(bus)

    async def publish(self, room_id: str, envelope: MessageEnvelope) -> None:
        self.published_count += 1
        for bus in list(self._topics.get(room_id, [])):
            if self.drop_probability and random.random() < self.drop_probability:  # noqa: S311
                continue
            if self.reorder_probability and random.random() < self.reorder_probability:  # noqa: S311
                # Entrega atrasada: outra mensagem passa à frente e o hold-back
                # do receptor precisa reordenar.
                asyncio.create_task(self._delayed(bus, room_id, envelope))  # noqa: RUF006
            else:
                await bus._deliver(room_id, envelope)

    async def _delayed(self, bus: InMemoryBus, room_id: str, envelope: MessageEnvelope) -> None:
        await asyncio.sleep(random.uniform(0.005, 0.030))  # noqa: S311
        await bus._deliver(room_id, envelope)

    # -- Sequenciador ------------------------------------------------------

    def next_seq(self, room_id: str) -> int:
        """Equivalente ao ``INCR`` do Redis.

        É atômico pelo mesmo motivo que os relógios em ``domain/clocks.py``: não
        há ``await`` no corpo, então nenhuma outra corrotina do event loop pode
        se intercalar entre a leitura e a escrita.
        """
        self._seq[room_id] += 1
        return self._seq[room_id]

    def current_seq(self, room_id: str) -> int:
        return self._seq[room_id]


class InMemoryBus:
    """Um nó ligado a um :class:`InMemoryBroker`."""

    def __init__(self, broker: InMemoryBroker, node_id: str = "node") -> None:
        self._broker = broker
        self.node_id = node_id
        self._handler: MessageHandler | None = None
        self._subscribed: set[str] = set()
        self._alive = True

    async def start(self) -> None:
        self._alive = True

    async def stop(self) -> None:
        self._alive = False
        self._broker._detach(self)
        self._subscribed.clear()

    def kill(self) -> None:
        """Mata o nó abruptamente, sem encerramento gracioso.

        É o que o teste de tolerância a falhas usa para reproduzir, em memória,
        o mesmo evento que ``scripts/kill_node.sh`` provoca na AWS.
        """
        self._alive = False
        self._broker._detach(self)

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def publish(self, room_id: str, envelope: MessageEnvelope) -> None:
        if not self._alive:
            raise ConnectionError("nó morto")
        await self._broker.publish(room_id, envelope)

    async def subscribe(self, room_id: str) -> None:
        if room_id not in self._subscribed:
            self._subscribed.add(room_id)
            self._broker._register(self, room_id)

    async def unsubscribe(self, room_id: str) -> None:
        self._subscribed.discard(room_id)
        self._broker._unregister(self, room_id)

    async def healthy(self) -> bool:
        return self._alive

    async def _deliver(self, room_id: str, envelope: MessageEnvelope) -> None:
        if self._alive and self._handler is not None:
            await self._handler(room_id, envelope)


class InMemorySequencer:
    def __init__(self, broker: InMemoryBroker) -> None:
        self._broker = broker

    async def next_seq(self, room_id: str) -> int:
        return self._broker.next_seq(room_id)

    async def current(self, room_id: str) -> int:
        return self._broker.current_seq(room_id)


class InMemoryMessageRepository:
    """Histórico em memória, indexado por sala e ordenado por ``seq``."""

    def __init__(self) -> None:
        self._by_room: dict[str, dict[int, MessageEnvelope]] = defaultdict(dict)
        self.save_count = 0

    async def save(self, envelope: MessageEnvelope) -> None:
        self._by_room[envelope.room_id][envelope.seq] = envelope
        self.save_count += 1

    async def backlog(
        self, room_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[MessageEnvelope]:
        room = self._by_room.get(room_id, {})
        seqs = sorted(s for s in room if s > after_seq)[:limit]
        return [room[s] for s in seqs]

    async def healthy(self) -> bool:
        return True


class InMemoryPresenceStore:
    def __init__(self, broker: InMemoryBroker) -> None:
        self._broker = broker

    async def join(self, room_id: str, member: Member) -> None:
        self._broker._presence[room_id][member.session_id] = (member, time.time())

    async def leave(self, room_id: str, session_id: str) -> None:
        self._broker._presence[room_id].pop(session_id, None)

    async def heartbeat(self, room_id: str, session_id: str) -> None:
        entry = self._broker._presence[room_id].get(session_id)
        if entry:
            self._broker._presence[room_id][session_id] = (entry[0], time.time())

    async def members(self, room_id: str) -> list[Member]:
        return [m for m, _ in self._broker._presence.get(room_id, {}).values()]

    async def rooms(self) -> list[RoomSummary]:
        return [
            RoomSummary(
                room_id=r,
                member_count=len(members),
                last_seq=self._broker.current_seq(r),
            )
            for r, members in self._broker._presence.items()
            if members
        ]

    async def sweep(self, max_age_seconds: int = 15) -> int:
        cutoff = time.time() - max_age_seconds
        removed = 0
        for room in list(self._broker._presence):
            stale = [s for s, (_, t) in self._broker._presence[room].items() if t < cutoff]
            for s in stale:
                del self._broker._presence[room][s]
                removed += 1
        return removed


class InMemoryNodeRegistry:
    def __init__(self, broker: InMemoryBroker) -> None:
        self._broker = broker

    async def heartbeat(self, info: NodeInfo) -> None:
        self._broker._nodes[info.node_id] = (info, time.time())

    async def alive(self, max_age_seconds: int = 15) -> list[NodeInfo]:
        cutoff = time.time() - max_age_seconds
        return [i for i, t in self._broker._nodes.values() if t >= cutoff]

    async def sweep(self, max_age_seconds: int = 15) -> int:
        cutoff = time.time() - max_age_seconds
        stale = [n for n, (_, t) in self._broker._nodes.items() if t < cutoff]
        for n in stale:
            del self._broker._nodes[n]
        return len(stale)


class InMemoryIdempotencyStore:
    def __init__(self, broker: InMemoryBroker) -> None:
        self._broker = broker

    async def claim(self, key: str, value: str, ttl_seconds: int = 300) -> str | None:
        now = time.time()
        existing = self._broker._idempotency.get(key)
        if existing and existing[1] > now:
            return existing[0]
        self._broker._idempotency[key] = (value, now + ttl_seconds)
        return None

    async def record(self, key: str, value: str, ttl_seconds: int = 300) -> None:
        self._broker._idempotency[key] = (value, time.time() + ttl_seconds)
