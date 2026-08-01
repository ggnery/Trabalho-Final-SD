"""Barramento publish-subscribe sobre Redis.

Este é o componente que materializa a **comunicação indireta** exigida pelo
critério EC2. Um nó publica em ``chat:room:{room_id}`` e recebe de volta o que
outros nós publicaram nos tópicos que assina. Nenhum nó conhece a identidade,
o endereço ou a quantidade dos demais — o desacoplamento no espaço é o que
permite ao Auto Scaling adicionar e remover instâncias sem reconfigurar nada.

Sobre a garantia de entrega
---------------------------
Redis Pub/Sub é *at-most-once*: uma mensagem publicada enquanto um nó está
desconectado é perdida **por aquele nó**. Isso é aceito conscientemente
(ADR-002): a durabilidade vem da camada de persistência (DynamoDB) e o cliente
recupera a lacuna reconectando com ``last_seq``. A alternativa — um canal
durável tipo SQS — custaria centenas de milissegundos de latência em um sistema
cujo requisito é tempo real.
"""

from __future__ import annotations

import asyncio
import contextlib
import random

import redis.asyncio as aioredis
import structlog
from redis.exceptions import RedisError

from salaviva.domain.models import MessageEnvelope
from salaviva.ports import MessageHandler

__all__ = ["ROOM_TOPIC", "RedisMessageBus"]

log = structlog.get_logger(__name__)

ROOM_TOPIC = "chat:room:{room_id}"


class RedisMessageBus:
    """Adaptador de :class:`salaviva.ports.MessageBus` para Redis Pub/Sub."""

    def __init__(
        self,
        redis_url: str,
        node_id: str,
        *,
        max_backoff: float = 30.0,
    ) -> None:
        self._url = redis_url
        self.node_id = node_id
        self._max_backoff = max_backoff

        self._redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._handler: MessageHandler | None = None
        self._reader_task: asyncio.Task | None = None

        self._subscribed: set[str] = set()
        self._connected = asyncio.Event()
        self._stopping = False

    # -- Ciclo de vida -----------------------------------------------------

    async def start(self) -> None:
        self._stopping = False
        self._reader_task = asyncio.create_task(self._run(), name="redis-bus-reader")
        # Espera a primeira conexão para que /readyz não aprove um nó que ainda
        # não é capaz de receber difusões.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._connected.wait(), timeout=10)

    async def stop(self) -> None:
        self._stopping = True
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self._pubsub:
            with contextlib.suppress(RedisError):
                await self._pubsub.aclose()
        if self._redis:
            with contextlib.suppress(RedisError):
                await self._redis.aclose()
        self._connected.clear()

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    # -- Operações ---------------------------------------------------------

    async def publish(self, room_id: str, envelope: MessageEnvelope) -> None:
        if self._redis is None:
            raise ConnectionError("barramento Redis indisponível")
        await self._redis.publish(
            ROOM_TOPIC.format(room_id=room_id),
            envelope.model_dump_json(),
        )

    async def subscribe(self, room_id: str) -> None:
        if room_id in self._subscribed:
            return
        self._subscribed.add(room_id)
        if self._pubsub is not None:
            await self._pubsub.subscribe(ROOM_TOPIC.format(room_id=room_id))
            log.info("topic_subscribed", room_id=room_id, node_id=self.node_id)

    async def unsubscribe(self, room_id: str) -> None:
        if room_id not in self._subscribed:
            return
        self._subscribed.discard(room_id)
        if self._pubsub is not None:
            with contextlib.suppress(RedisError):
                await self._pubsub.unsubscribe(ROOM_TOPIC.format(room_id=room_id))

    async def healthy(self) -> bool:
        if self._redis is None or not self._connected.is_set():
            return False
        try:
            return bool(await self._redis.ping())
        except (RedisError, OSError):
            return False

    # -- Laço de recepção com reconexão -----------------------------------

    async def _run(self) -> None:
        """Mantém a conexão viva, reconectando com backoff exponencial + jitter.

        O jitter é essencial e não cosmético: sem ele, os N nós do Auto Scaling
        — que perderam a conexão no mesmo instante — tentariam reconectar
        exatamente juntos, criando um pico sincronizado sobre um Redis que
        acabou de se recuperar. É o modo de falha em que uma recuperação
        parcial se transforma em uma queda total.
        """
        attempt = 0
        while not self._stopping:
            try:
                await self._connect()
                attempt = 0
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except (RedisError, OSError, ConnectionError) as exc:
                self._connected.clear()
                attempt += 1
                delay = min(0.5 * (2 ** min(attempt, 6)), self._max_backoff)
                delay *= 0.5 + random.random()  # noqa: S311 - jitter, não é uso criptográfico
                log.warning(
                    "redis_reconnecting",
                    node_id=self.node_id,
                    attempt=attempt,
                    delay_s=round(delay, 2),
                    error=str(exc),
                )
                await asyncio.sleep(delay)

    async def _connect(self) -> None:
        if self._redis is not None:
            with contextlib.suppress(RedisError):
                await self._redis.aclose()

        self._redis = aioredis.from_url(
            self._url,
            decode_responses=True,
            health_check_interval=10,
            socket_keepalive=True,
        )
        await self._redis.ping()

        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        # Restaura as assinaturas: após uma reconexão o Redis não lembra de nada.
        if self._subscribed:
            await self._pubsub.subscribe(*[ROOM_TOPIC.format(room_id=r) for r in self._subscribed])
        else:
            # O PubSub precisa de ao menos um canal para que `listen` produza
            # frames; um canal sentinela mantém o laço vivo em um nó ocioso.
            await self._pubsub.subscribe("chat:__keepalive__")

        self._connected.set()
        log.info("redis_connected", node_id=self.node_id, topics=len(self._subscribed))

    async def _read_loop(self) -> None:
        assert self._pubsub is not None  # noqa: S101 - invariante pós-_connect
        async for raw in self._pubsub.listen():
            if self._stopping:
                return
            if raw.get("type") != "message":
                continue
            channel: str = raw["channel"]
            if channel == "chat:__keepalive__":
                continue
            room_id = channel.removeprefix("chat:room:")
            try:
                envelope = MessageEnvelope.model_validate_json(raw["data"])
            except ValueError:
                log.error("envelope_invalido", channel=channel, node_id=self.node_id)
                continue
            if self._handler is not None:
                # Uma falha na entrega a um cliente não pode interromper o laço
                # de recepção do nó inteiro.
                try:
                    await self._handler(room_id, envelope)
                except Exception:
                    log.exception("erro_no_handler", room_id=room_id, seq=envelope.seq)
