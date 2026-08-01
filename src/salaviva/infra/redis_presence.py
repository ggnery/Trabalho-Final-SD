"""Presença por sala, sobre Redis.

Cada sala tem um *sorted set* ``chat:presence:{room_id}`` cujo **score é o epoch
do último heartbeat** do membro. Essa escolha de estrutura resolve três
problemas de uma vez:

* expirar membros vira ``ZREMRANGEBYSCORE`` — uma operação O(log n), sem varrer
  a lista nem manter um TTL por membro;
* listar quem está online vira ``ZRANGEBYSCORE`` a partir do corte;
* renovar presença vira ``ZADD``, que é idempotente.

A consequência arquitetural é a que mais importa: **nenhum nó precisa detectar
a morte de outro**. Quando uma instância EC2 é derrubada, seus membros
simplesmente param de renovar o score e são varridos em até um ciclo de sweeper.
Detecção de falha por ausência de renovação, em vez de por notificação, é o que
mantém o sistema sem acoplamento nó-a-nó.
"""

from __future__ import annotations

import time

import redis.asyncio as aioredis

from salaviva.domain.models import Member, RoomSummary
from salaviva.infra.redis_sequencer import SEQ_KEY

__all__ = ["PRESENCE_KEY", "RedisPresenceStore"]

PRESENCE_KEY = "chat:presence:{room_id}"
PRESENCE_INDEX = "chat:presence:index"
MEMBER_KEY = "chat:member:{room_id}:{session_id}"


class RedisPresenceStore:
    """Adaptador de :class:`salaviva.ports.PresenceStore`."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def join(self, room_id: str, member: Member) -> None:
        now = time.time()
        pipe = self._redis.pipeline()
        pipe.zadd(PRESENCE_KEY.format(room_id=room_id), {member.session_id: now})
        pipe.set(
            MEMBER_KEY.format(room_id=room_id, session_id=member.session_id),
            member.model_dump_json(),
            ex=300,
        )
        pipe.sadd(PRESENCE_INDEX, room_id)
        await pipe.execute()

    async def leave(self, room_id: str, session_id: str) -> None:
        pipe = self._redis.pipeline()
        pipe.zrem(PRESENCE_KEY.format(room_id=room_id), session_id)
        pipe.delete(MEMBER_KEY.format(room_id=room_id, session_id=session_id))
        await pipe.execute()

    async def heartbeat(self, room_id: str, session_id: str) -> None:
        now = time.time()
        pipe = self._redis.pipeline()
        pipe.zadd(PRESENCE_KEY.format(room_id=room_id), {session_id: now}, xx=True)
        pipe.expire(MEMBER_KEY.format(room_id=room_id, session_id=session_id), 300)
        await pipe.execute()

    async def members(self, room_id: str) -> list[Member]:
        session_ids = await self._redis.zrange(PRESENCE_KEY.format(room_id=room_id), 0, -1)
        if not session_ids:
            return []
        raw = await self._redis.mget(
            [MEMBER_KEY.format(room_id=room_id, session_id=s) for s in session_ids]
        )
        out: list[Member] = []
        for item in raw:
            if item:
                try:
                    out.append(Member.model_validate_json(item))
                except ValueError:  # pragma: no cover - dado corrompido
                    continue
        return out

    async def rooms(self) -> list[RoomSummary]:
        room_ids = await self._redis.smembers(PRESENCE_INDEX)
        if not room_ids:
            return []
        pipe = self._redis.pipeline()
        for r in room_ids:
            pipe.zcard(PRESENCE_KEY.format(room_id=r))
            pipe.get(SEQ_KEY.format(room_id=r))
        results = await pipe.execute()

        summaries: list[RoomSummary] = []
        for i, room_id in enumerate(room_ids):
            count = int(results[i * 2] or 0)
            last_seq = int(results[i * 2 + 1] or 0)
            if count or last_seq:
                summaries.append(
                    RoomSummary(room_id=room_id, member_count=count, last_seq=last_seq)
                )
        return sorted(summaries, key=lambda s: (-s.member_count, s.room_id))

    async def sweep(self, max_age_seconds: int = 15) -> int:
        """Remove membros cujo heartbeat expirou.

        Roda periodicamente em **todos** os nós. É intencional que seja
        redundante: se um único nó fosse responsável pela varredura, ele
        próprio seria um ponto único de falha para a limpeza de presença — e a
        morte desse nó deixaria membros fantasmas para sempre. Como
        ``ZREMRANGEBYSCORE`` é idempotente, a redundância não causa dano.
        """
        cutoff = time.time() - max_age_seconds
        room_ids = await self._redis.smembers(PRESENCE_INDEX)
        if not room_ids:
            return 0
        pipe = self._redis.pipeline()
        for r in room_ids:
            pipe.zremrangebyscore(PRESENCE_KEY.format(room_id=r), "-inf", cutoff)
        results = await pipe.execute()
        return sum(int(x or 0) for x in results)
