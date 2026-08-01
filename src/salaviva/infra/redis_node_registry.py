"""Registro de nós vivos, sobre Redis.

Cada nó publica seu estado a cada 5 s em ``chat:nodes`` (sorted set com score =
epoch do heartbeat) e o detalhe em ``chat:node:{id}``.

Este registro é **puramente observacional**: nenhum nó o consulta para decidir
para onde enviar algo — o roteamento é feito pelo Pub/Sub, que não precisa saber
quem existe. O registro serve ao painel ``/dashboard``, onde durante a
apresentação a plateia vê, ao vivo, a instância derrubada desaparecer e o
substituto criado pelo Auto Scaling surgir com um ``node_id`` novo.
"""

from __future__ import annotations

import time

import redis.asyncio as aioredis

from salaviva.domain.models import NodeInfo

__all__ = ["NODES_KEY", "RedisNodeRegistry"]

NODES_KEY = "chat:nodes"
NODE_DETAIL_KEY = "chat:node:{node_id}"


class RedisNodeRegistry:
    """Adaptador de :class:`salaviva.ports.NodeRegistry`."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def heartbeat(self, info: NodeInfo) -> None:
        pipe = self._redis.pipeline()
        pipe.zadd(NODES_KEY, {info.node_id: time.time()})
        pipe.set(NODE_DETAIL_KEY.format(node_id=info.node_id), info.model_dump_json(), ex=60)
        await pipe.execute()

    async def alive(self, max_age_seconds: int = 15) -> list[NodeInfo]:
        cutoff = time.time() - max_age_seconds
        node_ids = await self._redis.zrangebyscore(NODES_KEY, cutoff, "+inf")
        if not node_ids:
            return []
        raw = await self._redis.mget([NODE_DETAIL_KEY.format(node_id=n) for n in node_ids])
        out: list[NodeInfo] = []
        for item in raw:
            if item:
                try:
                    out.append(NodeInfo.model_validate_json(item))
                except ValueError:  # pragma: no cover
                    continue
        return sorted(out, key=lambda n: n.node_id)

    async def sweep(self, max_age_seconds: int = 15) -> int:
        cutoff = time.time() - max_age_seconds
        return int(await self._redis.zremrangebyscore(NODES_KEY, "-inf", cutoff))
