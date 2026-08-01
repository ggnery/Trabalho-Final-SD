"""Cliente de linha de comando do SalaViva.

Por que existe um cliente CLI, se já existe o cliente web
--------------------------------------------------------
O cliente web mostra que o chat *funciona*. Este cliente mostra **por que ele
funciona**: ele imprime, mensagem a mensagem, os três metadados de ordenação
distribuída que o servidor carimba (``seq``, ``lamport``, ``node_id``) e destaca
os pares de mensagens **concorrentes** detectados pelo relógio vetorial.

    [seq=143 | L=87 | node-a3f2] gabriel: olá

É este terminal que se projeta durante a apresentação: quando um nó é derrubado,
a mesma janela mostra a reconexão, o nó novo que assumiu e exatamente quais
``seq`` foram recuperados — a evidência visual de que nenhuma mensagem se perdeu.

Decisões de implementação que valem a pena registrar
----------------------------------------------------
1. **A fila de hold-back é a do próprio projeto** (``salaviva.domain.ordering``).
   Cliente e servidor compartilham a mesma implementação de "ordenado", então a
   demonstração não pode divergir da suíte de testes por acidente.
2. **Nada é renderizado direto do socket.** Toda mensagem recebida — tempo real,
   backlog de ``join`` ou resposta de ``resync`` — passa pela fila e só aparece
   quando ela libera. É isso que torna a chegada fora de ordem invisível ao
   usuário e o replay de reconexão idempotente.
3. **``ts`` nunca é usado para ordenar nem para medir latência.** Relógios de
   instâncias EC2 divergem mesmo sob NTP (ADR-003). A latência exibida é o
   round-trip local do ``ack``, medido com ``time.monotonic()``.
4. **A entrada do teclado roda em thread daemon.** Um ``input()`` bloqueante
   dentro do event loop travaria todas as corrotinas — inclusive o heartbeat —
   e impediria o encerramento limpo com Ctrl+C.

Uso típico na demonstração:

    salaviva-cli --user gabriel --room geral
    salaviva-cli --user projetor --room geral --observer
    salaviva-cli --user carga --room geral --auto 5
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

try:  # pragma: no cover - conveniência de execução
    from salaviva.domain.clocks import CausalOrder, VectorClock
    from salaviva.domain.ordering import HoldBackQueue
except ImportError:  # pragma: no cover - rodando sem `uv pip install -e .`
    # O cliente é executável direto do repositório (`python client/cli/salaviva_cli.py`)
    # durante a apresentação, sem depender de o pacote estar instalado no ambiente.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from salaviva.domain.clocks import CausalOrder, VectorClock
    from salaviva.domain.ordering import HoldBackQueue

__all__ = ["SalaVivaClient", "main"]

# --------------------------------------------------------------------------- #
# Constantes de comportamento
# --------------------------------------------------------------------------- #

GAP_TIMEOUT_S = 2.0
"""Tempo que uma lacuna pode persistir antes de o cliente pedir ``resync``.

O Pub/Sub do Redis é *at-most-once*: uma mensagem perdida no fan-out nunca
chegará sozinha. Esperar indefinidamente pela contiguidade travaria a sala."""

GAP_WATCHDOG_INTERVAL_S = 0.25
"""Granularidade do vigia de lacunas. Bem menor que ``GAP_TIMEOUT_S`` para que
o atraso observado seja o do protocolo, não o da amostragem."""

VECTOR_HISTORY_SIZE = 16
"""Quantos carimbos vetoriais recentes ficam guardados para comparação.

A concorrência é uma relação entre *pares* de eventos; comparar apenas com o
imediatamente anterior perderia o caso — comum com 3 nós — em que a mensagem
concorrente ficou duas ou três posições atrás na ordem total."""

BACKOFF_BASE_S = 0.5
BACKOFF_MAX_S = 30.0
JITTER_RATIO = 0.35
"""Backoff exponencial com jitter. O jitter existe para que N clientes que
perderam o mesmo nó não reconectem no mesmo instante e criem um *thundering
herd* sobre os nós sobreviventes — o modo de falha em que uma recuperação
parcial vira uma queda total."""

_jitter_source = random.SystemRandom()
"""Fonte do jitter de reconexão.

Usa a entropia do sistema operacional no lugar do gerador padrão. O jitter não
precisa ser imprevisível — precisa apenas ser diferente entre clientes —, mas o
custo é irrelevante (uma chamada por tentativa de reconexão) e evita carregar
uma supressão de lint a mais para alguém revisar depois."""

HTTP_TIMEOUT_S = 5.0
SEND_WAIT_TIMEOUT_S = 10.0
"""Tempo que uma mensagem digitada espera pela reconexão antes de ser descartada."""

RATE_LIMIT_PER_SECOND = 20
"""Limite por sessão imposto pelo servidor (FR-13); usado só para avisar o
usuário quando ``--auto`` pede mais do que isso."""

SLASH_HELP = """comandos disponíveis:
  /join <sala>    entra em uma sala (passa a ser a sala ativa)
  /leave <sala>   sai de uma sala, mantendo a conexão
  /rooms          lista as salas com presença ativa (HTTP /api/rooms)
  /nodes          lista os nós vivos do cluster (HTTP /api/nodes)
  /stats          seq atual, lacunas, reconexões e latência média
  /help           mostra esta ajuda
  /quit           encerra e imprime o resumo da sessão"""


# --------------------------------------------------------------------------- #
# Apresentação
# --------------------------------------------------------------------------- #


class Palette:
    """Cores ANSI, desligáveis com ``--no-color``.

    Existe como objeto — e não como constantes de módulo — porque a decisão de
    colorir depende de argumento de linha de comando e de o ``stdout`` ser um
    terminal: redirecionar a saída para um arquivo de log da apresentação não
    pode encher o arquivo de sequências de escape.
    """

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled

    def _paint(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.enabled else text

    def dim(self, text: str) -> str:
        return self._paint("2", text)

    def bold(self, text: str) -> str:
        return self._paint("1", text)

    def red(self, text: str) -> str:
        return self._paint("31", text)

    def green(self, text: str) -> str:
        return self._paint("32", text)

    def yellow(self, text: str) -> str:
        return self._paint("33", text)

    def blue(self, text: str) -> str:
        return self._paint("34", text)

    def magenta(self, text: str) -> str:
        return self._paint("35", text)

    def cyan(self, text: str) -> str:
        return self._paint("36", text)

    def badge(self, text: str) -> str:
        """Texto em vídeo reverso (preto sobre amarelo): o marcador que precisa
        ser legível no projetor a três metros de distância."""
        return self._paint("30;43;1", f" {text} ")

    def banner(self, text: str) -> str:
        """Faixa de destaque (branco sobre azul) para eventos de topologia."""
        return self._paint("97;44;1", f" {text} ")


# --------------------------------------------------------------------------- #
# Estado
# --------------------------------------------------------------------------- #


@dataclass
class Stats:
    """Contadores da sessão — a evidência numérica exibida no fim da demo."""

    received: int = 0
    """Mensagens recebidas do socket, antes da fila de hold-back."""

    rendered: int = 0
    """Mensagens efetivamente exibidas (liberadas pela fila)."""

    sent: int = 0
    reordered: int = 0
    """Mensagens que chegaram fora de ordem e ficaram retidas até a lacuna
    fechar. Prova que o hold-back está trabalhando de fato."""

    duplicates: int = 0
    """Descartadas pela fila por já terem sido entregues — o que torna o replay
    de reconexão seguro."""

    gaps: int = 0
    resyncs: int = 0
    forced: int = 0
    """Lacunas que não fecharam nem após ``resync``: perda real do at-most-once."""

    reconnections: int = 0
    concurrent: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def observe_latency(self, value_ms: float) -> None:
        self.latencies_ms.append(value_ms)

    @property
    def avg_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
        return ordered[index]


@dataclass
class RoomState:
    """Estado local de uma sala: é aqui que mora a garantia de ordem.

    Cada sala tem sua própria fila de hold-back porque o ``seq`` é atribuído por
    um contador **por sala** (``INCR chat:seq:{room}``); misturar salas na mesma
    fila criaria lacunas fantasmas.
    """

    room: str
    queue: HoldBackQueue[dict[str, Any]] = field(default_factory=HoldBackQueue)
    active: bool = True
    first_join: bool = True
    gap_since: float | None = None
    resync_sent: bool = False
    banner_pending: bool = False
    """Marcado ao refazer o ``join`` depois de uma queda: o próximo ``joined``
    desta sala imprime a faixa de reconexão com o total recuperado."""

    vectors: deque[tuple[int, str, dict[str, int]]] = field(
        default_factory=lambda: deque(maxlen=VECTOR_HISTORY_SIZE)
    )

    @property
    def last_seq(self) -> int:
        return self.queue.last_delivered


# --------------------------------------------------------------------------- #
# HTTP auxiliar (login e consultas)
# --------------------------------------------------------------------------- #


def _http_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Faz uma requisição HTTP JSON **bloqueante**.

    Só é chamada dentro de ``asyncio.to_thread``: bloquear o event loop travaria
    o laço de leitura do WebSocket e, com ele, o heartbeat da conexão.

    Usa apenas a biblioteca padrão de propósito — o cliente da apresentação não
    deve depender de nada além do que o servidor já exige.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"esquema de URL não suportado: {parsed.scheme!r}")

    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(  # noqa: S310 - esquema validado logo acima
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def http_base_from_ws(url: str) -> str:
    """Deriva a URL HTTP a partir da URL WebSocket.

    O login e as consultas ``/api/*`` vão para o mesmo ALB, na mesma porta:
    ``ws://`` vira ``http://`` e ``wss://`` vira ``https://``.
    """
    parsed = urllib.parse.urlparse(url)
    scheme = {"ws": "http", "wss": "https", "http": "http", "https": "https"}.get(
        parsed.scheme, "http"
    )
    return f"{scheme}://{parsed.netloc}"


def ws_endpoint(url: str, token: str) -> str:
    """Monta ``.../ws?token=<JWT>`` sem duplicar o caminho se o usuário já o deu."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/ws"):
        path = f"{path}/ws"
    query = urllib.parse.urlencode({"token": token})
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", query, ""))


# --------------------------------------------------------------------------- #
# Cliente
# --------------------------------------------------------------------------- #


class SalaVivaClient:
    """Sessão interativa de terminal contra um nó do cluster SalaViva.

    A instância é dona de todo o estado local: token, conexão corrente, estado
    por sala e contadores. As corrotinas de longa duração (entrada do teclado,
    vigia de lacunas, gerador de carga) sobrevivem às quedas de conexão — só o
    laço de leitura é recriado a cada reconexão.
    """

    def __init__(
        self,
        *,
        url: str,
        user: str,
        room: str,
        observer: bool = False,
        auto_rate: float = 0.0,
        color: bool = True,
    ) -> None:
        self.url = url
        self.http_base = http_base_from_ws(url)
        self.user = user
        self.observer = observer
        self.auto_rate = auto_rate
        self.palette = Palette(enabled=color and sys.stdout.isatty())

        self.stats = Stats()
        self.rooms: dict[str, RoomState] = {}
        self.active_room: str = room
        self.node_id: str = "?"
        self.session_id: str = "?"

        self._token: str = ""
        self._ws: ClientConnection | None = None
        self._stop = asyncio.Event()
        self._online = asyncio.Event()
        """Sinaliza "conectado **e** com as salas rejoinadas".

        Um envio precisa das duas coisas: publicar antes do ``join`` devolveria
        ``error/not_in_room``. Esperar por este evento é o que faz uma mensagem
        digitada durante a queda de um nó ser entregue depois da reconexão, em
        vez de ser descartada."""

        self._inflight: dict[str, float] = {}
        self._commands: asyncio.Queue[str | None] = asyncio.Queue()
        self._auto_counter = 0
        self._summary_printed = False
        self._prompt_enabled = sys.stdin.isatty()

        self.rooms[room] = RoomState(room=room)

    # -- saída ------------------------------------------------------------- #

    def emit(self, text: str) -> None:
        """Imprime uma linha sem destruir o que o usuário está digitando.

        Limpa a linha corrente, escreve o evento e redesenha o prompt. Sem isso,
        cada mensagem recebida embaralharia o texto em digitação — inaceitável
        em um terminal que está sendo projetado.
        """
        if self._prompt_enabled and self.palette.enabled:
            sys.stdout.write(f"\r\x1b[2K{text}\n{self._prompt()}")
        else:
            sys.stdout.write(f"{text}\n")
        sys.stdout.flush()

    def _prompt(self) -> str:
        """Prompt da linha de digitação: mostra a sala ativa."""
        if not self._prompt_enabled:
            return ""
        marker = "olho" if self.observer else self.active_room
        return self.palette.dim(f"{marker}> ")

    def _redraw_prompt(self) -> None:
        """Redesenha o prompt depois de uma linha de evento."""
        if self._prompt_enabled and self.palette.enabled:
            sys.stdout.write(self._prompt())
            sys.stdout.flush()

    # -- ciclo de vida ----------------------------------------------------- #

    async def run(self) -> None:
        """Autentica e mantém a sessão viva até ``/quit``, EOF ou Ctrl+C."""
        self._print_header()
        self._token = await self._login()

        background = [
            asyncio.create_task(self._input_loop(), name="entrada"),
            asyncio.create_task(self._gap_watchdog(), name="vigia-lacunas"),
        ]
        if self.auto_rate > 0 and not self.observer:
            background.append(asyncio.create_task(self._auto_sender(), name="carga"))

        try:
            await self._connection_loop()
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            self.print_summary()

    async def _login(self) -> str:
        """Obtém o JWT em ``POST /auth/login``.

        O token é *stateless*: vale em qualquer nó do Auto Scaling, e é por isso
        que a reconexão a um nó diferente não exige novo login — só quando o
        próprio token expira.
        """
        url = f"{self.http_base}/auth/login"
        payload = await asyncio.to_thread(_http_json, url, {"username": self.user})
        token = str(payload["token"])
        self.emit(self.palette.dim(f"autenticado como {self.user} em {self.http_base}"))
        return token

    async def _connection_loop(self) -> None:
        """Conecta, atende a sessão e reconecta com backoff exponencial + jitter.

        Este laço é o que entrega o requisito de tolerância a falhas do lado do
        cliente: quando a instância EC2 é derrubada, o socket morre, o laço
        espera o backoff, o ALB roteia para um nó vivo e o ``join`` é refeito com
        o ``last_seq`` local — recuperando exatamente o intervalo perdido.
        """
        attempt = 0
        first_connection = True

        while not self._stop.is_set():
            try:
                async with connect(
                    ws_endpoint(self.url, self._token),
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=1024,
                ) as websocket:
                    self._ws = websocket
                    attempt = 0
                    if not first_connection:
                        self.stats.reconnections += 1
                        for state in self.rooms.values():
                            state.banner_pending = state.active
                    first_connection = False
                    await self._session(websocket)
            except ConnectionClosed as exc:
                if exc.rcvd is not None and exc.rcvd.code == 4401:
                    # Token expirado: renovar antes de tentar de novo, senão a
                    # reconexão entra em laço eterno de 4401.
                    self.emit(self.palette.yellow("token expirado, reautenticando"))
                    with contextlib.suppress(OSError, urllib.error.URLError, KeyError):
                        self._token = await self._login()
                else:
                    self.emit(self.palette.yellow(f"conexão encerrada: {exc.reason or exc.code}"))
            except (OSError, TimeoutError, InvalidStatus, WebSocketException) as exc:
                self.emit(self.palette.yellow(f"falha ao conectar: {type(exc).__name__}: {exc}"))
            finally:
                self._ws = None
                self._online.clear()

            if self._stop.is_set():
                break

            delay = self._backoff(attempt)
            attempt += 1
            self.emit(self.palette.dim(f"reconectando em {delay:.1f}s (tentativa {attempt})"))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Atraso exponencial truncado, com jitter proporcional."""
        base = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2**attempt))
        return base + _jitter_source.uniform(0, base * JITTER_RATIO)

    async def _session(self, websocket: ClientConnection) -> None:
        """Atende uma conexão: welcome, (re)join das salas e laço de leitura."""
        welcome = json.loads(await websocket.recv())
        self._handle_welcome(welcome)
        await self._rejoin_all()
        self._online.set()

        async for raw in websocket:
            frame = json.loads(raw)
            await self._handle_frame(frame)

    async def _rejoin_all(self) -> None:
        """Refaz ``join`` em todas as salas ativas com o ``last_seq`` local.

        É este ``last_seq`` — e não um timestamp — que define o que precisa ser
        recuperado: ele é o último ``seq`` **renderizado**, então o servidor
        devolve exatamente a diferença, sem lacuna e sem duplicata.
        """
        for state in self.rooms.values():
            if state.active:
                await self._send({"type": "join", "room": state.room, "last_seq": state.last_seq})

    # -- envio ------------------------------------------------------------- #

    async def _send(self, frame: dict[str, Any]) -> bool:
        """Envia um frame; devolve ``False`` se não há conexão no momento."""
        websocket = self._ws
        if websocket is None:
            return False
        try:
            await websocket.send(json.dumps(frame))
        except (ConnectionClosed, WebSocketException):
            return False
        return True

    async def _send_message(self, content: str) -> None:
        """Publica uma mensagem na sala ativa, medindo o round-trip do ``ack``.

        Se a sessão estiver fora do ar (o caso da instância derrubada), aguarda
        até ``SEND_WAIT_TIMEOUT_S`` pela reconexão em vez de descartar o texto —
        a mensagem digitada durante a queda chega quando o novo nó assume.
        """
        if self.observer:
            self.emit(self.palette.yellow("modo observador: somente leitura"))
            return
        if not self._online.is_set():
            self.emit(self.palette.dim("sem conexão: a mensagem sai assim que a sessão voltar..."))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._online.wait(), timeout=SEND_WAIT_TIMEOUT_S)
        client_msg_id = uuid.uuid4().hex[:16]
        self._inflight[client_msg_id] = time.monotonic()
        ok = await self._send(
            {
                "type": "send",
                "room": self.active_room,
                "content": content,
                "client_msg_id": client_msg_id,
            }
        )
        if ok:
            self.stats.sent += 1
        else:
            self._inflight.pop(client_msg_id, None)
            self.emit(self.palette.red("sem conexão: mensagem não enviada"))

    # -- recepção ---------------------------------------------------------- #

    def _handle_welcome(self, frame: dict[str, Any]) -> None:
        """Registra o nó que atendeu o handshake.

        O ``node_id`` é o dado mais importante desta linha: é ele que denuncia,
        na tela, que o ALB entregou a conexão a outra instância.
        """
        self.node_id = str(frame.get("node_id", "?"))
        self.session_id = str(frame.get("session_id", "?"))
        self.emit(
            self.palette.green("conectado")
            + self.palette.dim(" ao nó ")
            + self.palette.bold(self.node_id)
            + self.palette.dim(f" (sessão {self.session_id[:8]})")
        )

    async def _handle_frame(self, frame: dict[str, Any]) -> None:
        """Despacha um frame do servidor. Ver ``docs/protocolo.md``."""
        kind = frame.get("type")

        if kind == "message":
            self._ingest(str(frame.get("room_id", self.active_room)), frame)
        elif kind == "joined":
            self._handle_joined(frame)
        elif kind == "ack":
            self._handle_ack(frame)
        elif kind == "left":
            self.emit(self.palette.dim(f"saiu da sala {frame.get('room')}"))
        elif kind == "presence_update":
            self._handle_presence(frame)
        elif kind == "typing":
            self.emit(self.palette.dim(f"{frame.get('user')} está digitando..."))
        elif kind == "error":
            self._handle_error(frame)
        elif kind == "welcome":
            self._handle_welcome(frame)
        elif kind == "pong":
            pass  # heartbeat do servidor: a ausência é que importa, não a chegada
        else:
            self.emit(self.palette.dim(f"frame desconhecido: {kind}"))

    def _handle_joined(self, frame: dict[str, Any]) -> None:
        """Processa ``joined``: alinha a fila e renderiza o backlog.

        Na primeira entrada em uma sala, a fila é reposicionada para o início do
        backlog recebido (ou para o ``last_seq`` da sala, se não houver
        histórico). Sem isso, entrar em uma sala com 500 mensagens antigas
        deixaria o cliente eternamente esperando o ``seq`` 1.
        """
        room = str(frame.get("room", self.active_room))
        state = self._room(room)
        state.active = True
        backlog: list[dict[str, Any]] = list(frame.get("backlog", []))
        node_id = str(frame.get("node_id", self.node_id))

        server_last = int(frame.get("last_seq", 0))
        baseline = int(backlog[0]["seq"]) - 1 if backlog else server_last

        if state.first_join:
            state.queue.reset(max(0, baseline))
            state.first_join = False
        elif server_last < state.queue.last_delivered:
            # A sala regrediu: o contador de sequência foi reiniciado (Redis novo,
            # cluster recriado do zero). Sem realinhar, o cliente descartaria como
            # duplicata tudo o que viesse a seguir.
            self.emit(
                self.palette.yellow(
                    f"sequência de #{room} regrediu para {server_last} "
                    f"(cluster reiniciado): realinhando a fila"
                )
            )
            state.queue.reset(max(0, baseline))

        recovered = 0
        for envelope in backlog:
            recovered += self._ingest(room, envelope)

        if state.banner_pending:
            state.banner_pending = False
            self._print_reconnect_banner(room, node_id, backlog, recovered)
        else:
            members = frame.get("members", [])
            self.emit(
                self.palette.green(f"entrou em #{room}")
                + self.palette.dim(f" — nó {node_id}, último seq {frame.get('last_seq')}, ")
                + self.palette.dim(f"{len(members)} membro(s): {self._format_members(members)}")
            )

    def _print_reconnect_banner(
        self,
        room: str,
        node_id: str,
        backlog: list[dict[str, Any]],
        recovered: int,
    ) -> None:
        """A faixa que se projeta durante a simulação de falha.

        É o resumo, em uma linha, de tudo o que o projeto promete: o cliente
        migrou para outro nó e as mensagens produzidas durante a queda foram
        recuperadas sem lacuna.
        """
        if backlog:
            seqs = [int(env["seq"]) for env in backlog]
            faixa = f"(seq {min(seqs)}..{max(seqs)})"
        else:
            faixa = "(nenhuma lacuna a recuperar)"
        self.emit(
            self.palette.banner(
                f"RECONECTADO ao nó {node_id} — recuperadas {recovered} mensagens {faixa}"
            )
            + self.palette.dim(f" #{room}")
        )

    def _handle_ack(self, frame: dict[str, Any]) -> None:
        """Registra a latência do envio e sinaliza deduplicação do servidor."""
        client_msg_id = str(frame.get("client_msg_id", ""))
        started = self._inflight.pop(client_msg_id, None)
        if started is not None:
            self.stats.observe_latency((time.monotonic() - started) * 1000)
        if frame.get("duplicate"):
            self.emit(
                self.palette.yellow(
                    f"ack duplicado (idempotência do servidor) seq={frame.get('seq')}"
                )
            )

    def _handle_presence(self, frame: dict[str, Any]) -> None:
        """Exibe entrada/saída de participantes com o nó de cada um."""
        verbo = "entrou" if frame.get("event") == "join" else "saiu"
        members = frame.get("members", [])
        self.emit(
            self.palette.dim(
                f"* {frame.get('user')} {verbo} de #{frame.get('room')} "
                f"({len(members)} presente(s): {self._format_members(members)})"
            )
        )

    def _handle_error(self, frame: dict[str, Any]) -> None:
        """Mostra o erro de protocolo sem derrubar a sessão.

        Erro de domínio é resposta, não queda: ``rate_limited`` e
        ``service_unavailable`` são transitórios e a conexão segue viva.
        """
        code = str(frame.get("code", "?"))
        self.emit(self.palette.red(f"erro/{code}: {frame.get('message')}"))

    def _format_members(self, members: list[dict[str, Any]]) -> str:
        """Lista ``usuário@nó``.

        O ``@nó`` não é decoração: é a evidência, na própria tela, de que os
        participantes de uma mesma sala estão espalhados por instâncias EC2
        diferentes e ainda assim veem a mesma conversa.
        """
        return ", ".join(f"{m.get('user')}@{m.get('node_id')}" for m in members) or "-"

    # -- ordenação --------------------------------------------------------- #

    def _room(self, room: str) -> RoomState:
        """Devolve (criando se preciso) o estado local de uma sala."""
        state = self.rooms.get(room)
        if state is None:
            state = RoomState(room=room)
            self.rooms[room] = state
        return state

    def _ingest(self, room: str, envelope: dict[str, Any]) -> int:
        """Alimenta a fila de hold-back e renderiza o que ela liberar.

        Todas as fontes de mensagem convergem aqui — tempo real, backlog de
        ``join`` e resposta de ``resync``. Um único ponto de entrada é o que
        garante que backlog sobreposto a tempo real não produza duplicata: a
        fila é idempotente por ``seq``.

        Devolve quantas mensagens foram efetivamente exibidas.
        """
        state = self._room(room)
        seq = int(envelope.get("seq", 0))
        self.stats.received += 1

        expected = state.queue.expected
        pending_before = state.queue.pending_count
        released = state.queue.offer(seq, envelope)

        if seq < expected:
            self.stats.duplicates += 1  # já entregue: replay de reconexão
            return 0
        if not released and state.queue.pending_count == pending_before:
            self.stats.duplicates += 1  # já estava retido no buffer
            return 0
        if seq > expected:
            self.stats.reordered += 1  # fora de ordem: retida até a lacuna fechar

        for item in released:
            self._render_message(state, item)
        self._refresh_gap_state(state)
        return len(released)

    def _refresh_gap_state(self, state: RoomState) -> None:
        """Abre ou fecha a janela de lacuna de uma sala."""
        if state.queue.has_gap:
            if state.gap_since is None:
                state.gap_since = time.monotonic()
                self.stats.gaps += 1
                missing = state.queue.missing_range()
                if missing is not None:
                    self.emit(
                        self.palette.yellow(
                            f"lacuna em #{state.room}: faltam seq {missing[0]}..{missing[1]} "
                            f"({state.queue.pending_count} retida(s))"
                        )
                    )
        else:
            state.gap_since = None
            state.resync_sent = False

    async def _gap_watchdog(self) -> None:
        """Pede ``resync`` quando uma lacuna não fecha sozinha.

        Duas etapas deliberadas: primeiro pede ao servidor o intervalo faltante
        (o caso comum, em que o Pub/Sub perdeu a entrega para aquele nó); se nem
        assim fechar, libera o que está retido aceitando a perda — porque travar
        a sala para sempre é pior que exibir uma lacuna e contabilizá-la.
        """
        while not self._stop.is_set():
            await asyncio.sleep(GAP_WATCHDOG_INTERVAL_S)
            now = time.monotonic()
            for state in list(self.rooms.values()):
                if state.gap_since is None or now - state.gap_since < GAP_TIMEOUT_S:
                    continue

                if not state.resync_sent:
                    after_seq = state.queue.last_delivered
                    if await self._send(
                        {"type": "resync", "room": state.room, "after_seq": after_seq}
                    ):
                        self.stats.resyncs += 1
                        state.resync_sent = True
                        state.gap_since = now
                        self.emit(
                            self.palette.yellow(
                                f"resync solicitado em #{state.room} após seq {after_seq}"
                            )
                        )
                    continue

                released = state.queue.force_release()
                self.stats.forced += 1
                self.emit(
                    self.palette.red(
                        f"lacuna não fechou em #{state.room}: "
                        f"liberando {len(released)} mensagem(ns) com perda assumida"
                    )
                )
                for item in released:
                    self._render_message(state, item)
                state.gap_since = None
                state.resync_sent = False

    # -- renderização ------------------------------------------------------ #

    def _render_message(self, state: RoomState, envelope: dict[str, Any]) -> None:
        """Exibe uma mensagem já liberada pela fila, com os relógios lógicos.

        O formato é fixo e deliberadamente denso — ``seq`` (ordem total),
        ``L`` (Lamport, *happened-before*) e o nó de origem — porque é ele que
        permite ao avaliador conferir, olhando duas janelas lado a lado, que
        clientes diferentes veem a **mesma** ordem total.
        """
        seq = int(envelope.get("seq", 0))
        lamport = int(envelope.get("lamport", 0))
        node_id = str(envelope.get("node_id", "?"))
        sender = str(envelope.get("sender", "?"))
        content = str(envelope.get("content", ""))
        vector: dict[str, int] = dict(envelope.get("vector_clock") or {})

        concurrent_with = self._detect_concurrency(state, seq, vector)
        state.vectors.append((seq, sender, vector))
        self.stats.rendered += 1

        cabecalho = (
            self.palette.dim("[")
            + self.palette.cyan(f"seq={seq}")
            + self.palette.dim(" | ")
            + self.palette.magenta(f"L={lamport}")
            + self.palette.dim(" | ")
            + self.palette.blue(node_id)
            + self.palette.dim("]")
        )
        sala = f" {self.palette.dim('#' + state.room)}" if len(self.rooms) > 1 else ""
        autor = self.palette.bold(self.palette.green(sender))

        marca = ""
        if concurrent_with is not None:
            self.stats.concurrent += 1
            marca = " " + self.palette.badge(f"CONCORRENTE com seq={concurrent_with}")

        self.emit(f"{cabecalho}{sala} {autor}: {content}{marca}")

    def _detect_concurrency(self, state: RoomState, seq: int, vector: dict[str, int]) -> int | None:
        """Compara o carimbo vetorial com os anteriores e devolve o ``seq`` do
        evento concorrente mais recente, se houver.

        Concorrência (``a ∥ b``) significa que nenhuma das duas mensagens causou
        a outra: elas foram produzidas em nós distintos sem que um tivesse visto
        o evento do outro. É o fenômeno que o relógio escalar de Lamport não
        consegue distinguir de causalidade — e a razão de o envelope carregar
        também um relógio vetorial (ADR-005).
        """
        if not vector:
            return None
        for previous_seq, _sender, previous_vector in reversed(state.vectors):
            if previous_seq == seq or not previous_vector:
                continue
            if VectorClock.compare(previous_vector, vector) is CausalOrder.CONCURRENT:
                return previous_seq
        return None

    # -- entrada do usuário ------------------------------------------------ #

    async def _input_loop(self) -> None:
        """Consome as linhas digitadas, vindas da thread de ``stdin``."""
        loop = asyncio.get_running_loop()
        _spawn_stdin_reader(loop, self._commands)
        self._redraw_prompt()

        while not self._stop.is_set():
            line = await self._commands.get()
            if line is None:  # EOF (Ctrl+D ou entrada redirecionada esgotada)
                self._stop.set()
                break
            line = line.strip()
            if not line:
                self._redraw_prompt()
                continue
            if line.startswith("/"):
                await self._handle_command(line)
            elif self.observer:
                self.emit(self.palette.yellow("modo observador: use /help para os comandos"))
            else:
                await self._send_message(line)

        await self._shutdown()

    async def _handle_command(self, line: str) -> None:
        """Interpreta os comandos de barra."""
        parts = line.split()
        command, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")

        if command == "/quit":
            self._stop.set()
        elif command == "/help":
            self.emit(SLASH_HELP)
        elif command == "/join":
            await self._cmd_join(arg)
        elif command == "/leave":
            await self._cmd_leave(arg or self.active_room)
        elif command == "/rooms":
            await self._cmd_rooms()
        elif command == "/nodes":
            await self._cmd_nodes()
        elif command == "/stats":
            self._cmd_stats()
        else:
            self.emit(self.palette.red(f"comando desconhecido: {command} (use /help)"))

    async def _cmd_join(self, room: str) -> None:
        """``/join``: entra na sala e a torna a sala ativa do prompt."""
        if not room:
            self.emit(self.palette.red("uso: /join <sala>"))
            return
        state = self._room(room)
        state.active = True
        self.active_room = room
        await self._send({"type": "join", "room": room, "last_seq": state.last_seq})

    async def _cmd_leave(self, room: str) -> None:
        """``/leave``: sai da sala mantendo a conexão e as demais salas."""
        state = self.rooms.get(room)
        if state is None or not state.active:
            self.emit(self.palette.red(f"não está em #{room}"))
            return
        state.active = False
        # O estado da fila é preservado de propósito: um /join posterior retoma
        # do último seq exibido, sem reprocessar o que já foi visto.
        await self._send({"type": "leave", "room": room})
        if self.active_room == room:
            restantes = [r for r, s in self.rooms.items() if s.active]
            self.active_room = restantes[0] if restantes else room

    async def _cmd_rooms(self) -> None:
        """``/rooms``: consulta ``GET /api/rooms`` (visão global, via Redis)."""
        try:
            payload = await asyncio.to_thread(_http_json, f"{self.http_base}/api/rooms")
        except (OSError, urllib.error.URLError, ValueError) as exc:
            self.emit(self.palette.red(f"falha ao listar salas: {exc}"))
            return
        self.emit(self.palette.bold(f"salas ativas: {payload.get('count', 0)}"))
        for room in payload.get("rooms", []):
            self.emit(
                f"  #{room.get('room_id')}  membros={room.get('member_count')}  "
                f"last_seq={room.get('last_seq')}"
            )

    async def _cmd_nodes(self) -> None:
        """``/nodes``: consulta ``GET /api/nodes``.

        É o comando da demonstração de falha: a instância derrubada some da
        lista em até um ciclo de sweeper e o substituto do Auto Scaling
        aparece com um ``node_id`` novo.
        """
        try:
            payload = await asyncio.to_thread(_http_json, f"{self.http_base}/api/nodes")
        except (OSError, urllib.error.URLError, ValueError) as exc:
            self.emit(self.palette.red(f"falha ao listar nós: {exc}"))
            return
        self.emit(
            self.palette.bold(f"nós vivos: {payload.get('count', 0)}")
            + self.palette.dim(f" (atendido por {payload.get('self')})")
        )
        for node in payload.get("nodes", []):
            marca = " <- este" if node.get("node_id") == self.node_id else ""
            self.emit(
                f"  {node.get('node_id')}  conexões={node.get('connections')}  "
                f"salas={node.get('rooms')}  lamport={node.get('lamport')}  "
                f"uptime={float(node.get('uptime_seconds', 0)):.0f}s{marca}"
            )

    def _cmd_stats(self) -> None:
        """``/stats``: estado de ordenação e saúde da sessão."""
        seqs = ", ".join(
            f"#{room}={state.last_seq}" for room, state in self.rooms.items() if state.active
        )
        self.emit(self.palette.bold("estado da sessão"))
        self.emit(f"  nó atual ............ {self.node_id}")
        self.emit(f"  seq por sala ........ {seqs or '-'}")
        self.emit(f"  lacunas detectadas .. {self.stats.gaps} (resyncs: {self.stats.resyncs})")
        self.emit(f"  reconexões .......... {self.stats.reconnections}")
        self.emit(
            f"  latência do ack ..... média {self.stats.avg_latency_ms:.1f} ms, "
            f"p95 {self.stats.p95_latency_ms:.1f} ms "
            f"({len(self.stats.latencies_ms)} amostra(s))"
        )
        self.emit(f"  concorrentes ........ {self.stats.concurrent}")

    async def _auto_sender(self) -> None:
        """Gera carga sintética: ``--auto N`` mensagens por segundo.

        Serve para que a demonstração tenha tráfego contínuo no instante em que
        a instância EC2 é derrubada — sem tráfego, a recuperação seria invisível,
        porque não haveria nada a recuperar.
        """
        interval = 1.0 / self.auto_rate
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            if not self._online.is_set():
                continue  # durante a queda a carga pausa; retoma sozinha ao reconectar
            self._auto_counter += 1
            await self._send_message(f"[auto {self._auto_counter:04d}] {self.user}")

    async def _shutdown(self) -> None:
        """Fecha o socket para desbloquear o laço de leitura."""
        self._stop.set()
        websocket = self._ws
        if websocket is not None:
            with contextlib.suppress(ConnectionClosed, WebSocketException, OSError):
                await websocket.close(code=1000, reason="encerrado pelo usuário")

    # -- resumo ------------------------------------------------------------ #

    def _print_header(self) -> None:
        """Cabeçalho de abertura: deixa o contexto da demo visível na tela."""
        modo = "observador (somente leitura)" if self.observer else "interativo"
        if self.auto_rate > 0:
            modo += (
                " (--auto ignorado: observador não publica)"
                if self.observer
                else f" + carga automática {self.auto_rate:g} msg/s"
            )
        self.emit(self.palette.bold("SalaViva CLI"))
        self.emit(self.palette.dim(f"  destino: {self.url}   usuário: {self.user}"))
        self.emit(self.palette.dim(f"  sala inicial: {self.active_room}   modo: {modo}"))
        self.emit(self.palette.dim("  /help lista os comandos"))

    def print_summary(self) -> None:
        """Resumo final: os números que sustentam as afirmações da apresentação."""
        if self._summary_printed:
            return
        self._summary_printed = True
        seqs = ", ".join(f"#{room}={state.last_seq}" for room, state in self.rooms.items())
        linhas = [
            "",
            self.palette.bold("=== RESUMO DA SESSÃO ==="),
            f"  mensagens recebidas ......... {self.stats.received}",
            f"  mensagens exibidas .......... {self.stats.rendered}",
            f"  mensagens enviadas .......... {self.stats.sent}",
            f"  fora de ordem reordenadas ... {self.stats.reordered}",
            f"  duplicatas descartadas ...... {self.stats.duplicates}",
            f"  lacunas detectadas .......... {self.stats.gaps} "
            f"(resyncs: {self.stats.resyncs}, liberadas com perda: {self.stats.forced})",
            f"  reconexões .................. {self.stats.reconnections}",
            f"  concorrentes detectadas ..... {self.stats.concurrent}",
            f"  latência média do ack ....... {self.stats.avg_latency_ms:.1f} ms "
            f"(p95 {self.stats.p95_latency_ms:.1f} ms)",
            f"  último seq por sala ......... {seqs or '-'}",
            f"  último nó ................... {self.node_id}",
        ]
        # Sem redesenhar o prompt: a sessão acabou.
        sys.stdout.write("\n".join(linhas) + "\n")
        sys.stdout.flush()


def _spawn_stdin_reader(
    loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str | None]
) -> threading.Thread:
    """Bombeia ``stdin`` para uma fila do event loop, em thread daemon.

    Daemon por um motivo prático: ao encerrar com ``/quit`` ou Ctrl+C, a thread
    pode estar bloqueada em ``readline()``. Uma thread não-daemon obrigaria o
    usuário a pressionar Enter para o programa morrer — péssimo em apresentação.
    """

    def _pump() -> None:
        for line in sys.stdin:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, line.rstrip("\n"))
            except RuntimeError:
                return  # o loop já foi fechado: nada mais a entregar
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(queue.put_nowait, None)

    thread = threading.Thread(target=_pump, name="salaviva-stdin", daemon=True)
    thread.start()
    return thread


# --------------------------------------------------------------------------- #
# Linha de comando
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Monta o parser da linha de comando."""
    parser = argparse.ArgumentParser(
        prog="salaviva-cli",
        description=(
            "Cliente de terminal do SalaViva. Exibe seq (ordem total), relógio de "
            "Lamport e nó de origem de cada mensagem, e destaca as concorrentes "
            "detectadas pelo relógio vetorial."
        ),
        epilog="exemplo: salaviva-cli --user gabriel --room geral --url ws://localhost:8080",
    )
    parser.add_argument(
        "--url",
        default="ws://localhost:8080",
        help="URL do WebSocket (ALB ou nó). Padrão: ws://localhost:8080",
    )
    parser.add_argument("--user", required=True, help="nome de usuário para o login")
    parser.add_argument("--room", default="geral", help="sala inicial. Padrão: geral")
    parser.add_argument(
        "--observer",
        action="store_true",
        help="somente leitura: ideal para projetar durante a apresentação",
    )
    parser.add_argument(
        "--auto",
        type=float,
        default=0.0,
        metavar="N",
        help="envia N mensagens automáticas por segundo (gera carga para a demo)",
    )
    parser.add_argument("--no-color", action="store_true", help="desliga as cores ANSI")
    return parser


def main() -> int:
    """Ponto de entrada do console script ``salaviva-cli``."""
    args = build_parser().parse_args()

    if args.auto < 0:
        build_parser().error("--auto não aceita valor negativo")
    if args.auto > RATE_LIMIT_PER_SECOND:
        sys.stderr.write(
            f"aviso: --auto {args.auto:g} excede o limite de "
            f"{RATE_LIMIT_PER_SECOND} msg/s por sessão; o servidor vai rejeitar o excedente\n"
        )

    client = SalaVivaClient(
        url=args.url,
        user=args.user,
        room=args.room,
        observer=args.observer,
        auto_rate=args.auto,
        color=not args.no_color,
    )

    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        client.print_summary()
    except (OSError, urllib.error.URLError) as exc:
        sys.stderr.write(f"não foi possível falar com {client.http_base}: {exc}\n")
        return 1
    except (KeyError, ValueError) as exc:
        sys.stderr.write(f"resposta inesperada do servidor: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
