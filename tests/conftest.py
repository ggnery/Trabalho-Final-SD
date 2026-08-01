"""Fixtures compartilhadas.

O núcleo destas fixtures é o :func:`cluster`, que sobe **N nós do SalaViva no
mesmo processo Python**, ligados por um :class:`InMemoryBroker` compartilhado.
Cada nó tem seu próprio relógio de Lamport, seu próprio relógio vetorial e seu
próprio gerenciador de conexões — exatamente como instâncias EC2 distintas
teriam.

Isso é o que permite verificar propriedades genuinamente distribuídas (ordem
total idêntica entre nós, ausência de perda após queda de nó, detecção de
concorrência) sem Redis, sem AWS e sem Docker: um ``pytest`` na máquina de
qualquer avaliador reproduz a prova.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import pytest

from salaviva.app.chat_service import ChatService
from salaviva.config import Settings
from salaviva.infra.memory.adapters import (
    InMemoryBroker,
    InMemoryBus,
    InMemoryIdempotencyStore,
    InMemoryMessageRepository,
    InMemoryNodeRegistry,
    InMemoryPresenceStore,
    InMemorySequencer,
)
from salaviva.ws.manager import ClientConnection, ConnectionManager


class FakeWebSocketState:
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


@dataclass
class FakeWebSocket:
    """WebSocket de teste que apenas registra o que foi enviado."""

    sent: list[dict] = field(default_factory=list)
    closed: bool = False
    close_code: int | None = None

    @property
    def client_state(self):
        from starlette.websockets import WebSocketState

        return WebSocketState.DISCONNECTED if self.closed else WebSocketState.CONNECTED

    async def send_json(self, payload: dict) -> None:
        if self.closed:
            raise ConnectionError("socket fechado")
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code

    # -- Auxiliares de asserção -------------------------------------------

    def frames(self, frame_type: str) -> list[dict]:
        return [f for f in self.sent if f.get("type") == frame_type]

    def messages(self) -> list[dict]:
        return self.frames("message")

    def seqs(self) -> list[int]:
        """Sequência de ``seq`` na ordem em que este cliente os recebeu."""
        return [m["seq"] for m in self.messages()]


class Node:
    """Um nó simulado, com o serviço e a lista de clientes locais."""

    def __init__(
        self,
        node_id: str,
        broker: InMemoryBroker,
        settings: Settings,
        repository: InMemoryMessageRepository,
    ) -> None:
        self.node_id = node_id
        self.broker = broker
        self.manager = ConnectionManager(node_id)
        self.bus = InMemoryBus(broker, node_id)
        # O repositório é COMPARTILHADO entre os nós, como o DynamoDB real é.
        # Se cada nó tivesse o seu, o replay de reconexão em outro nó não
        # encontraria nada — e o teste de tolerância a falhas passaria a
        # verificar uma arquitetura que não é a do sistema.
        self.repository = repository
        node_settings = settings.model_copy(update={"node_id": node_id})
        self.service = ChatService(
            settings=node_settings,
            manager=self.manager,
            bus=self.bus,
            sequencer=InMemorySequencer(broker),
            repository=self.repository,
            presence=InMemoryPresenceStore(broker),
            idempotency=InMemoryIdempotencyStore(broker),
            node_registry=InMemoryNodeRegistry(broker),
        )

    async def start(self) -> None:
        # Liga apenas o barramento; os laços de fundo (sweeper, heartbeat) não
        # são iniciados para que os testes não dependam de tempo de relógio.
        self.service.bus.on_message(self.service._on_bus_message)
        await self.service.bus.start()

    async def connect(self, user: str) -> tuple[ClientConnection, FakeWebSocket]:
        ws = FakeWebSocket()
        conn = ClientConnection(ws, uuid.uuid4().hex, user, self.node_id)  # type: ignore[arg-type]
        self.manager.add(conn)
        return conn, ws

    async def drain(self) -> None:
        """Aguarda as gravações assíncronas pendentes.

        A persistência acontece fora do caminho crítico (ADR-008), em Tasks
        paralelas. Um teste que consulte o histórico logo após o envio precisa
        esperá-las — do contrário estaria verificando uma corrida, não a
        propriedade que quer provar.
        """
        pendentes = list(self.service._pending_saves)
        if pendentes:
            await asyncio.gather(*pendentes, return_exceptions=True)

    def kill(self) -> None:
        """Simula a morte abrupta da instância (equivale a terminar a EC2)."""
        self.bus.kill()


class Cluster:
    """Conjunto de nós compartilhando um broker e um repositório."""

    def __init__(
        self, nodes: list[Node], broker: InMemoryBroker, repository: InMemoryMessageRepository
    ) -> None:
        self.nodes = nodes
        self.broker = broker
        self.repository = repository

    def __getitem__(self, index: int) -> Node:
        return self.nodes[index]

    def __len__(self) -> int:
        return len(self.nodes)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        redis_url="memory://",
        persistence_enabled=False,
        log_json=False,
        jwt_secret="segredo-de-teste",
        rate_limit_per_second=1000,
        rate_limit_burst=2000,
    )


@pytest.fixture
def broker() -> InMemoryBroker:
    return InMemoryBroker()


@pytest.fixture
async def cluster(broker: InMemoryBroker, settings: Settings):
    """Cluster de 3 nós — a configuração padrão do projeto na AWS."""
    repository = InMemoryMessageRepository()
    nodes = [Node(f"node-{c}", broker, settings, repository) for c in "abc"]
    for node in nodes:
        await node.start()
    yield Cluster(nodes, broker, repository)
    for node in nodes:
        await node.bus.stop()


@pytest.fixture
async def make_cluster(settings: Settings):
    """Fábrica de clusters com broker configurável (reordenação, perda)."""
    created: list[Node] = []

    async def _make(
        count: int = 3, *, reorder_probability: float = 0.0, drop_probability: float = 0.0
    ) -> Cluster:
        broker = InMemoryBroker(
            reorder_probability=reorder_probability, drop_probability=drop_probability
        )
        repository = InMemoryMessageRepository()
        nodes = [Node(f"node-{i}", broker, settings, repository) for i in range(count)]
        for node in nodes:
            await node.start()
        created.extend(nodes)
        return Cluster(nodes, broker, repository)

    yield _make
    for node in created:
        await node.bus.stop()
