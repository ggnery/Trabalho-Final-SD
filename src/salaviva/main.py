"""Composition root: monta o nó e liga as peças.

Este é o único lugar do sistema que decide **quais** implementações concretas
das portas serão usadas. Todo o resto do código depende apenas dos ``Protocol``
em ``ports.py``.

Dois modos de operação, escolhidos por configuração e não por detecção:

* ``SALAVIVA_REDIS_URL=memory://`` — nó autônomo com adaptadores em memória.
  Não precisa de Redis nem de AWS. É o plano B da apresentação e o modo de
  desenvolvimento rápido.
* qualquer outra URL — modo cluster, com Redis real e DynamoDB (ou
  ``SALAVIVA_PERSISTENCE_ENABLED=false`` para rodar sem AWS).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Query, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from salaviva.api import auth, health, rooms
from salaviva.app.chat_service import ChatService
from salaviva.config import Settings, get_settings
from salaviva.infra.dynamo_repository import DynamoMessageRepository, NullMessageRepository
from salaviva.infra.memory.adapters import (
    InMemoryBroker,
    InMemoryBus,
    InMemoryIdempotencyStore,
    InMemoryMessageRepository,
    InMemoryNodeRegistry,
    InMemoryPresenceStore,
    InMemorySequencer,
)
from salaviva.infra.redis_bus import RedisMessageBus
from salaviva.infra.redis_node_registry import RedisNodeRegistry
from salaviva.infra.redis_presence import RedisPresenceStore
from salaviva.infra.redis_sequencer import RedisIdempotencyStore, RedisSequencer
from salaviva.logging_setup import configure_logging
from salaviva.ws.connection import serve_websocket
from salaviva.ws.manager import ConnectionManager

__all__ = ["build_service", "create_app"]

log = structlog.get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def build_service(settings: Settings) -> tuple[ChatService, aioredis.Redis | None]:
    """Instancia o serviço com os adaptadores adequados ao modo configurado."""
    manager = ConnectionManager(settings.node_id)

    if settings.redis_url.startswith("memory://"):
        broker = InMemoryBroker()
        bus = InMemoryBus(broker, settings.node_id)
        service = ChatService(
            settings=settings,
            manager=manager,
            bus=bus,
            sequencer=InMemorySequencer(broker),
            # Mesmo em modo standalone o flag é respeitado, para que o
            # comportamento observado local e na nuvem seja o mesmo sob a mesma
            # configuração — a diferença entre ambientes deve vir só das
            # variáveis, nunca de um caminho de código que decide sozinho.
            repository=InMemoryMessageRepository()
            if settings.persistence_enabled
            else NullMessageRepository(),
            presence=InMemoryPresenceStore(broker),
            idempotency=InMemoryIdempotencyStore(broker),
            node_registry=InMemoryNodeRegistry(broker),
        )
        log.info("modo_standalone", node_id=settings.node_id)
        return service, None

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    repository = (
        DynamoMessageRepository(
            table_name=settings.messages_table,
            region=settings.aws_region,
            endpoint_url=settings.dynamo_endpoint_url,
            ttl_seconds=settings.dynamo_ttl_seconds,
        )
        if settings.persistence_enabled
        else NullMessageRepository()
    )

    service = ChatService(
        settings=settings,
        manager=manager,
        bus=RedisMessageBus(
            settings.redis_url, settings.node_id, max_backoff=settings.redis_max_backoff
        ),
        sequencer=RedisSequencer(redis),
        repository=repository,
        presence=RedisPresenceStore(redis),
        idempotency=RedisIdempotencyStore(redis),
        node_registry=RedisNodeRegistry(redis),
    )
    return service, redis


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json, settings.node_id)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service, redis = build_service(settings)
        app.state.service = service
        app.state.redis = redis
        app.state.settings = settings

        if settings.persistence_enabled and settings.dynamo_endpoint_url:
            # Só no Compose com DynamoDB Local. Na AWS a tabela vem do Terraform.
            with contextlib.suppress(Exception):
                await service.repository.ensure_tables()

        await service.start()
        log.info(
            "no_pronto",
            node_id=settings.node_id,
            environment=settings.environment,
            port=settings.port,
        )
        try:
            yield
        finally:
            await service.stop()
            if redis is not None:
                with contextlib.suppress(Exception):
                    await redis.aclose()

    app = FastAPI(
        title="SalaViva",
        description=(
            "Chat distribuído em tempo real com salas (Pub/Sub) — "
            "Projeto Final de Sistemas Distribuídos"
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    # Publicado já na construção — e não apenas no lifespan — para que routers
    # que leem `app.state.settings` funcionem mesmo quando a aplicação é montada
    # sem executar o ciclo de vida (por exemplo, ao gerar o schema OpenAPI).
    app.state.settings = settings

    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(rooms.router)

    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
        token: str = Query(default=""),
    ) -> None:
        await serve_websocket(
            websocket,
            token,
            websocket.app.state.service,
            websocket.app.state.settings,
        )

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "dashboard.html")

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


app = create_app()


def run() -> None:  # pragma: no cover - ponto de entrada
    # Import tardio: o uvicorn só é necessário quando o processo é iniciado
    # como servidor. Importar no topo obrigaria os testes e o cliente CLI —
    # que apenas importam o pacote — a carregar o servidor HTTP inteiro.
    import uvicorn  # noqa: PLC0415

    settings = get_settings()
    uvicorn.run(
        "salaviva.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        # Um worker por nó, deliberadamente. Cada nó mantém em memória o mapa de
        # conexões e o relógio de Lamport; múltiplos workers no mesmo host
        # criariam relógios divergentes sem canal entre eles, quebrando a
        # correspondência 1:1 entre node_id e processo que o algoritmo pressupõe.
        workers=1,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
