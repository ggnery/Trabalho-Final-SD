"""Sequenciador de ordem total e store de idempotência, sobre Redis.

O sequenciador é o árbitro da ordem total do sistema (ADR-003). ``INCR`` é
atômico e o Redis executa comandos em uma única thread, portanto as requisições
concorrentes de N nós são serializadas pelo próprio servidor — sem lock
distribuído, sem retry, sem o problema ABA.

O custo dessa escolha é um ponto de serialização por sala. O teto prático é o
throughput de ``INCR`` do Redis (~100 k ops/s), ordens de magnitude acima de
qualquer sala de chat real, e as salas são independentes entre si — então o
sistema continua escalando na dimensão que de fato cresce.
"""

from __future__ import annotations

import redis.asyncio as aioredis

__all__ = ["SEQ_KEY", "RedisIdempotencyStore", "RedisSequencer"]

SEQ_KEY = "chat:seq:{room_id}"
DEDUPE_KEY = "chat:dedupe:{key}"


class RedisSequencer:
    """Atribui ``seq`` monotônico e único por sala."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def next_seq(self, room_id: str) -> int:
        """Reserva o próximo número de sequência da sala.

        Uma única operação de rede, atômica no servidor. É a operação mais
        crítica do caminho quente: ela define a ordem que todos os clientes
        verão, e acontece antes de qualquer difusão ou persistência.
        """
        return int(await self._redis.incr(SEQ_KEY.format(room_id=room_id)))

    async def current(self, room_id: str) -> int:
        value = await self._redis.get(SEQ_KEY.format(room_id=room_id))
        return int(value) if value else 0


class RedisIdempotencyStore:
    """Deduplicação de envios por ``client_msg_id`` (FR-9).

    Usa ``SET key value NX EX ttl``: a reivindicação e o teste de existência
    acontecem na mesma operação atômica. Fazer ``GET`` seguido de ``SET`` abriria
    uma janela em que dois reenvios simultâneos passariam ambos — exatamente a
    duplicata que este store existe para impedir.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def claim(self, key: str, value: str, ttl_seconds: int = 300) -> str | None:
        full = DEDUPE_KEY.format(key=key)
        acquired = await self._redis.set(full, value, nx=True, ex=ttl_seconds)
        if acquired:
            return None
        return await self._redis.get(full)

    async def record(self, key: str, value: str, ttl_seconds: int = 300) -> None:
        await self._redis.set(DEDUPE_KEY.format(key=key), value, ex=ttl_seconds)
