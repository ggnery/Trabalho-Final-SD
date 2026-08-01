"""Testes end-to-end contra o cluster real (Docker Compose).

Exigem o cluster ativo: ``make up`` (ou ``docker compose up -d``). São os únicos
testes que exercitam Redis de verdade, WebSockets de verdade e a morte de um
contêiner de verdade — o mesmo evento que ``scripts/kill_node.sh --aws`` provoca
ao terminar uma instância EC2.

Rodar com::

    pytest tests/e2e -m e2e

Os testes em ``tests/integration`` provam as mesmas propriedades com
adaptadores em memória e rodam em qualquer máquina; estes provam que a
implementação Redis/DynamoDB honra os mesmos contratos.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import uuid

import httpx
import pytest
import websockets

from salaviva.domain.ordering import HoldBackQueue

pytestmark = pytest.mark.e2e

BASE = os.getenv("SALAVIVA_E2E_URL", "http://localhost:8080")
WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")
NOS = ("node-a", "node-b", "node-c")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


async def token(user: str) -> str:
    async with httpx.AsyncClient(base_url=BASE, timeout=10) as c:
        resp = await c.post("/auth/login", json={"username": user})
        resp.raise_for_status()
        return resp.json()["token"]


class Cliente:
    """Cliente WebSocket de teste, com fila de recepção em segundo plano."""

    def __init__(self, user: str, porta: int | None = None) -> None:
        self.user = user
        self.porta = porta
        self.node_id = ""
        self.recebidas: list[dict] = []
        self._ws = None
        self._task: asyncio.Task | None = None
        self._eventos: asyncio.Queue[dict] = asyncio.Queue()

    @property
    def _url_base(self) -> str:
        return f"ws://localhost:{self.porta}" if self.porta else WS_BASE

    async def conectar(self) -> None:
        tok = await token(self.user)
        self._ws = await websockets.connect(f"{self._url_base}/ws?token={tok}", open_timeout=10)
        self._task = asyncio.create_task(self._ler())
        boas_vindas = await self.esperar("welcome")
        self.node_id = boas_vindas["node_id"]

    async def _ler(self) -> None:
        # O fim da conexão é o resultado esperado em vários destes testes — o
        # nó pode ser derrubado de propósito no meio da leitura. Suprimimos o
        # erro aqui e deixamos as asserções do teste decidirem se a queda era
        # o comportamento correto.
        with contextlib.suppress(Exception):
            async for raw in self._ws:  # type: ignore[union-attr]
                frame = json.loads(raw)
                if frame["type"] == "message":
                    self.recebidas.append(frame)
                await self._eventos.put(frame)

    async def esperar(self, tipo: str, timeout: float = 10) -> dict:
        async def _buscar() -> dict:
            while True:
                frame = await self._eventos.get()
                if frame["type"] == tipo:
                    return frame

        return await asyncio.wait_for(_buscar(), timeout)

    async def entrar(self, sala: str, last_seq: int = 0) -> dict:
        await self._ws.send(json.dumps({"type": "join", "room": sala, "last_seq": last_seq}))  # type: ignore[union-attr]
        return await self.esperar("joined")

    async def enviar(self, sala: str, texto: str) -> None:
        await self._ws.send(  # type: ignore[union-attr]
            json.dumps(
                {
                    "type": "send",
                    "room": sala,
                    "content": texto,
                    "client_msg_id": uuid.uuid4().hex,
                }
            )
        )

    @property
    def seqs(self) -> list[int]:
        return [m["seq"] for m in self.recebidas]

    async def fechar(self) -> None:
        if self._task:
            self._task.cancel()
        if self._ws:
            await self._ws.close()


async def esperar_ate(condicao, timeout: float = 90, intervalo: float = 2) -> bool:
    """Aguarda ``condicao()`` virar verdadeira; devolve se conseguiu."""
    restante = timeout
    while restante > 0:
        if await condicao():
            return True
        await asyncio.sleep(intervalo)
        restante -= intervalo
    return False


def docker(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


@pytest.fixture(autouse=True)
async def cluster_disponivel():
    """Pula os testes se o cluster não estiver no ar."""
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=5) as c:
            if (await c.get("/readyz")).status_code != 200:
                pytest.skip("cluster não está pronto — rode `make up`")
    except (httpx.HTTPError, OSError):
        pytest.skip(f"cluster inacessível em {BASE} — rode `make up`")


# ---------------------------------------------------------------------------
# Distribuição real
# ---------------------------------------------------------------------------


async def test_clientes_caem_em_nos_diferentes():
    """Conectando direto nas portas dos nós, cada cliente é atendido por um nó distinto."""
    clientes = [Cliente(f"user{i}", porta=8001 + i) for i in range(3)]
    try:
        for c in clientes:
            await c.conectar()
        ids = {c.node_id for c in clientes}
        assert len(ids) == 3, f"esperava 3 nós distintos, obtive {ids}"
    finally:
        for c in clientes:
            await c.fechar()


async def test_mensagem_atravessa_o_cluster_via_redis():
    """Comunicação em grupo real: Pub/Sub do Redis leva a mensagem entre nós."""
    sala = f"e2e-{uuid.uuid4().hex[:8]}"
    clientes = [Cliente(f"u{i}", porta=8001 + i) for i in range(3)]
    try:
        for c in clientes:
            await c.conectar()
            await c.entrar(sala)

        await clientes[0].enviar(sala, "atravessa o cluster")

        for c in clientes:
            await c.esperar("message")
            assert c.recebidas[-1]["content"] == "atravessa o cluster"

        origens = {c.recebidas[-1]["node_id"] for c in clientes}
        assert origens == {clientes[0].node_id}, "todos devem ver a mesma origem"
    finally:
        for c in clientes:
            await c.fechar()


async def test_ordem_total_identica_no_cluster_real():
    """120 mensagens intercaladas entre 3 nós, com Redis real.

    Verifica as duas propriedades separadamente, porque elas são distintas:

    1. **Ordem de entrega idêntica entre clientes.** Todos recebem a mesma
       sequência de ``seq``, na mesma ordem de chegada.
    2. **Ordem total após reordenação no cliente.** A chegada bruta *pode* vir
       fora de ordem — dois nós fazem ``INCR`` e depois ``PUBLISH``, e nada
       obriga a ordem dos publishes a coincidir com a dos INCR. O nó que obteve
       ``seq=3`` pode publicar antes do que obteve ``seq=2``.

    A propriedade (2) é o que a fila de hold-back garante, e é por isso que ela
    existe no cliente: o ``seq`` define a ordem *correta*, não a de chegada.
    """
    sala = f"e2e-{uuid.uuid4().hex[:8]}"
    total = 120
    clientes = [Cliente(f"o{i}", porta=8001 + i) for i in range(3)]
    try:
        for c in clientes:
            await c.conectar()
            await c.entrar(sala)

        for i in range(total):
            await clientes[i % 3].enviar(sala, f"m{i}")

        chegou = await esperar_ate(
            lambda: asyncio.sleep(0, result=all(len(c.recebidas) >= total for c in clientes)),
            timeout=45,
            intervalo=1,
        )
        assert chegou, [len(c.recebidas) for c in clientes]

        sequencias = [c.seqs[:total] for c in clientes]
        referencia = sequencias[0]

        # (1) Entrega uniforme: ninguém vê uma sequência diferente de ninguém.
        for i, seqs in enumerate(sequencias[1:], start=1):
            assert seqs == referencia, f"cliente {i} viu ordem diferente"
        assert len(set(referencia)) == total, "seq repetido"
        assert sorted(referencia) == list(range(1, total + 1)), "faltou ou sobrou seq"

        # (2) Ordem total após a fila de hold-back — o que o cliente renderiza.
        for indice, seqs in enumerate(sequencias):
            fila: HoldBackQueue[int] = HoldBackQueue(start_seq=0, max_buffer=1000)
            ordenada: list[int] = []
            for seq in seqs:
                ordenada.extend(fila.offer(seq, seq))
            assert ordenada == list(range(1, total + 1)), (
                f"cliente {indice} não restaurou a ordem total"
            )
    finally:
        for c in clientes:
            await c.fechar()


# ---------------------------------------------------------------------------
# Tolerância a falhas (critério EC3)
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_derrubar_no_nao_perde_mensagem():
    """Mata o contêiner node-b e verifica que nada se perde.

    Reproduz exatamente o que o professor pode pedir na apresentação. Ao final,
    o histórico da sala precisa estar contíguo e o cliente órfão precisa
    recuperar a lacuna ao reconectar informando o ``last_seq``.
    """
    sala = f"falha-{uuid.uuid4().hex[:8]}"
    ana = Cliente("ana", porta=8001)  # node-a, sobrevivente
    bruno = Cliente("bruno", porta=8002)  # node-b, será derrubado
    try:
        await ana.conectar()
        await bruno.conectar()
        await ana.entrar(sala)
        await bruno.entrar(sala)

        for i in range(5):
            await ana.enviar(sala, f"antes-{i}")
        await esperar_ate(
            lambda: asyncio.sleep(0, result=len(bruno.recebidas) >= 5), timeout=20, intervalo=0.5
        )
        ultimo_visto = max(bruno.seqs)
        assert ultimo_visto == 5

        # --- a falha ---
        docker("kill", "node-b")

        for i in range(5):
            await ana.enviar(sala, f"durante-{i}")
        await esperar_ate(
            lambda: asyncio.sleep(0, result=len(ana.recebidas) >= 10), timeout=20, intervalo=0.5
        )
        assert len(ana.recebidas) >= 10, "o nó sobrevivente continua entregando"

        # --- a recuperação: bruno reconecta em outro nó ---
        bruno_novo = Cliente("bruno", porta=8003)  # node-c
        await bruno_novo.conectar()
        frame = await bruno_novo.entrar(sala, last_seq=ultimo_visto)
        recuperadas = [m["seq"] for m in frame["backlog"]]

        assert recuperadas == [6, 7, 8, 9, 10], f"backlog inesperado: {recuperadas}"
        assert bruno.seqs + recuperadas == list(range(1, 11))
        await bruno_novo.fechar()

        # --- o histórico está íntegro ---
        async with httpx.AsyncClient(base_url=BASE, timeout=10) as c:
            hist = (await c.get(f"/api/rooms/{sala}/messages")).json()
        assert hist["contiguous"] is True, "houve lacuna na sequência"
        assert hist["count"] == 10
    finally:
        await ana.fechar()
        await bruno.fechar()
        docker("compose", "start", "node-b")
        await esperar_ate(
            lambda: _no_vivo("node-b"),
            timeout=90,
            intervalo=3,
        )


async def _no_vivo(node_id_parcial: str) -> bool:
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=5) as c:
            dados = (await c.get("/api/nodes")).json()
        return any(node_id_parcial in n["node_id"] for n in dados["nodes"])
    except (httpx.HTTPError, OSError, KeyError):
        return False


@pytest.mark.slow
async def test_no_derrubado_some_do_registro_e_volta():
    """O registro de nós reflete a falha e a recuperação sem ninguém notificar nada."""
    assert await esperar_ate(lambda: _no_vivo("node-b"), timeout=60, intervalo=2), (
        "node-b deveria estar vivo no início"
    )

    docker("kill", "node-b")
    sumiu = await esperar_ate(
        lambda: _negar(_no_vivo("node-b")),
        timeout=60,
        intervalo=2,
    )

    docker("compose", "start", "node-b")
    voltou = await esperar_ate(lambda: _no_vivo("node-b"), timeout=120, intervalo=3)

    assert sumiu, "o nó morto deveria sumir do registro em até um ciclo de sweeper"
    assert voltou, "o nó reiniciado deveria reaparecer no registro"


async def _negar(coro) -> bool:
    return not await coro
