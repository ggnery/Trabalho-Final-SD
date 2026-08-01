"""Gerador de carga e verificador de ordem total do SalaViva.

Abre N conexões WebSocket concorrentes distribuídas em M salas, faz cada cliente
virtual publicar a uma taxa configurável e mede o que a disciplina cobra:
latência fim a fim, tempo de handshake, throughput, conexões estabelecidas
versus falhas — e, principalmente, **se a ordem total sobrevive à carga**.

Por que a verificação de ordem é o resultado central
----------------------------------------------------
Latência e throughput dizem se o sistema é *rápido*. Só a verificação de ordem
diz se ele está *correto*. O requisito FR-5 afirma que dois clientes quaisquer
da mesma sala veem a mesma subsequência de mensagens, mesmo conectados a nós
físicos diferentes — e é exatamente sob concorrência alta que uma implementação
errada falharia. Um teste de carga que só reporta percentis mediria a velocidade
com que o sistema entrega mensagens possivelmente na ordem errada.

O que o script faz com o que recebe, na ordem exata:

1. autentica todos os clientes virtuais antes da rampa (o custo do login não
   contamina a medição do handshake WebSocket);
2. abre as conexões em rampa, para não estourar o limite de descritores de
   arquivo de uma vez só;
3. cada cliente entra em uma sala, registra o ``last_seq`` corrente como
   **linha de base** e passa a gravar todo ``seq`` que chega;
4. cada envio carrega um ``client_msg_id`` único; a latência fim a fim é o
   intervalo entre o envio e a chegada do **eco daquela mesma mensagem pelo
   Pub/Sub** — não o ``ack``, que é local ao nó e mediria menos do que o
   caminho real (ADR-004);
5. ao final, reconstrói o que cada cliente teria renderizado (fila de hold-back
   = ordenar por ``seq`` e descartar duplicatas) e compara os clientes entre si.

Uso típico:

    python -m loadtest.run_load --url http://localhost:8000 --clients 500
    python -m loadtest.run_load --url http://alb-xxxx.us-east-1.elb.amazonaws.com \\
        --clients 1200 --rooms 20 --rate 0.5 --ramp 60 --duration 120

Consulte ``loadtest/README.md`` — em especial a seção sobre ``ulimit -n``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import random
import sys
import time
import uuid
from array import array
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import websockets
from websockets.exceptions import WebSocketException

try:  # pragma: no cover - ``resource`` não existe no Windows
    import resource
except ImportError:  # pragma: no cover
    resource = None  # type: ignore[assignment]

__all__ = [
    "Config",
    "LoadRunner",
    "main",
    "percentil",
    "verificar_ordem_total",
]

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------

META_P95_MS: Final[float] = 200.0
"""Meta de latência fim a fim p95 (requirements.md § Performance)."""

META_P99_MS: Final[float] = 500.0
META_HANDSHAKE_P95_MS: Final[float] = 300.0

LIMITE_SERVIDOR_MSG_S: Final[float] = 20.0
"""Rate limit por sessão no servidor (FR-13). Acima disso o teste mede o
rejeitador, não o chat."""

_ESQUEMA_WS: Final[dict[str, str]] = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}
_ESQUEMA_HTTP: Final[dict[str, str]] = {
    "http": "http",
    "https": "https",
    "ws": "http",
    "wss": "https",
}


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Config:
    """Parâmetros de uma execução de carga."""

    url: str
    clients: int
    rooms: int
    duration: float
    rate: float
    ramp: float
    out: Path
    drain: float
    payload: int
    room_prefix: str
    user_prefix: str
    reconnect: bool
    connect_timeout: float
    login_concurrency: int
    max_divergencias: int
    seed: int
    quiet: bool
    resync_espera: float = 1.5
    """Segundos aguardados após pedir `resync`, antes de encerrar a sessão.
    Precisa acomodar a consulta ao histórico durável sob carga."""

    @property
    def base_http(self) -> str:
        """Base HTTP para ``POST /auth/login``, derivada de ``--url``."""
        partes = urlparse.urlsplit(self.url)
        esquema = _ESQUEMA_HTTP.get(partes.scheme)
        if esquema is None:
            raise ValueError(f"esquema de URL não suportado: {partes.scheme!r}")
        return urlparse.urlunsplit((esquema, partes.netloc, partes.path.rstrip("/"), "", ""))

    @property
    def base_ws(self) -> str:
        """Base WebSocket para ``/ws``, derivada de ``--url``."""
        partes = urlparse.urlsplit(self.url)
        esquema = _ESQUEMA_WS.get(partes.scheme)
        if esquema is None:
            raise ValueError(f"esquema de URL não suportado: {partes.scheme!r}")
        return urlparse.urlunsplit((esquema, partes.netloc, partes.path.rstrip("/"), "", ""))


# --------------------------------------------------------------------------
# Estado por cliente virtual
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ClientStats:
    """Tudo o que um cliente virtual observou durante a execução.

    ``seqs`` guarda a **ordem de chegada crua**, não a ordem de exibição. É essa
    distinção que permite medir duas coisas diferentes: quanto o Pub/Sub entrega
    fora de ordem (diagnóstico) e se a ordem total se sustenta depois da fila de
    hold-back (o veredito).
    """

    indice: int
    usuario: str
    sala: str

    token: str | None = None
    node_id: str | None = None
    erro: str | None = None

    conectou: bool = False
    conexao_tcp_ms: float = 0.0
    handshake_ms: float = 0.0

    sessoes: int = 0
    """Quantas vezes este cliente concluiu o handshake. Maior que 1 significa
    que ele foi derrubado e reconectou."""

    quedas: int = 0
    """Sessões já estabelecidas que caíram."""

    reconexoes_falhas: int = 0
    """Tentativas de reconexão que nem chegaram a conectar — enquanto o nó
    ainda está fora e o Auto Scaling não substituiu a instância."""

    baseline_seq: int = 0
    """Último ``seq`` da sala no instante do ``join``. Tudo abaixo disso é
    histórico anterior ao teste e não entra na verificação."""

    ultimo_seq: int = 0
    seqs: array = field(default_factory=lambda: array("q"))

    enviadas: int = 0
    acks: int = 0
    ecos: int = 0
    em_voo: dict[str, float] = field(default_factory=dict)
    erros_protocolo: Counter[str] = field(default_factory=Counter)


# --------------------------------------------------------------------------
# Estatística
# --------------------------------------------------------------------------


def percentil(ordenados: list[float], p: float) -> float:
    """Percentil ``p`` (0 a 100) de uma lista **já ordenada**, com interpolação.

    Exige a lista ordenada de propósito: quem chama ordena uma única vez e pede
    vários percentis, em vez de pagar um ``sort`` por percentil.
    """
    if not ordenados:
        return 0.0
    if len(ordenados) == 1:
        return ordenados[0]
    posicao = (len(ordenados) - 1) * (p / 100.0)
    inferior = math.floor(posicao)
    superior = math.ceil(posicao)
    if inferior == superior:
        return ordenados[inferior]
    peso = posicao - inferior
    return ordenados[inferior] * (1 - peso) + ordenados[superior] * peso


def resumo_estatistico(amostras: list[float]) -> dict[str, float | int]:
    """Percentis usuais de uma amostra de latências, em milissegundos."""
    if not amostras:
        return {
            "amostras": 0,
            "min": 0.0,
            "media": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "max": 0.0,
        }
    ordenados = sorted(amostras)
    return {
        "amostras": len(ordenados),
        "min": round(ordenados[0], 2),
        "media": round(sum(ordenados) / len(ordenados), 2),
        "p50": round(percentil(ordenados, 50), 2),
        "p90": round(percentil(ordenados, 90), 2),
        "p95": round(percentil(ordenados, 95), 2),
        "p99": round(percentil(ordenados, 99), 2),
        "max": round(ordenados[-1], 2),
    }


# --------------------------------------------------------------------------
# Verificação de ordem total — o coração do teste
# --------------------------------------------------------------------------


def _aplicar_hold_back(seqs: array) -> list[int]:
    """Reproduz o que a fila de hold-back do cliente renderizaria.

    A regra do protocolo (``docs/protocolo.md`` § Algoritmo do cliente) é
    ordenar por ``seq`` e descartar duplicatas — exatamente ``sorted(set(...))``.
    Reproduzi-la aqui é o que torna a verificação uma prova sobre o *sistema*, e
    não sobre a sorte de chegada dos pacotes.
    """
    return sorted(set(seqs))


def _chegadas_fora_de_ordem(seqs: array) -> int:
    """Quantas vezes um ``seq`` chegou menor que o anterior (diagnóstico)."""
    return sum(1 for i in range(1, len(seqs)) if seqs[i] < seqs[i - 1])


@dataclass(slots=True)
class _ResultadoSala:
    """Verificação de uma sala isolada."""

    sala: str
    clientes: int
    janela: tuple[int, int]
    mensagens: int
    divergencias: list[dict[str, Any]]


def _verificar_sala(
    sala: str, participantes: list[ClientStats], renderizado: dict[int, list[int]]
) -> _ResultadoSala | None:
    """Compara os clientes de uma sala dentro da janela comum de observação.

    Devolve ``None`` quando a sala não tem quórum: com um único observador, ou
    sem faixa de ``seq`` em comum, a afirmação "todos veem o mesmo" é vacuamente
    verdadeira — reportá-la como sucesso inflaria o resultado.
    """
    if len(participantes) < 2:
        return None

    limite_inferior = max(lista[0] for lista in renderizado.values())
    limite_superior = min(lista[-1] for lista in renderizado.values())
    if limite_superior < limite_inferior:
        return None

    esperado = list(range(limite_inferior, limite_superior + 1))
    divergencias: list[dict[str, Any]] = []

    for cliente in participantes:
        janela = [s for s in renderizado[cliente.indice] if limite_inferior <= s <= limite_superior]
        if janela == esperado:
            continue

        faltando = sorted(set(esperado) - set(janela))
        sobrando = sorted(set(janela) - set(esperado))
        divergencias.append(
            {
                "sala": sala,
                "cliente": cliente.usuario,
                "node_id": cliente.node_id,
                "tipo": "lacuna" if faltando else "excedente",
                "janela": [limite_inferior, limite_superior],
                "esperados": len(esperado),
                "recebidos": len(janela),
                "seq_faltando": faltando[:10],
                "seq_excedente": sobrando[:10],
                "duplicatas_na_chegada": len(cliente.seqs) - len(set(cliente.seqs)),
            }
        )

    return _ResultadoSala(
        sala=sala,
        clientes=len(participantes),
        janela=(limite_inferior, limite_superior),
        mensagens=len(esperado),
        divergencias=divergencias,
    )


def verificar_ordem_total(
    clientes: list[ClientStats], max_divergencias: int = 20
) -> dict[str, Any]:
    """Verifica, sala a sala, se a ordem total se manteve sob carga.

    Para cada sala, considera apenas a **janela comum de observação** — do maior
    ``seq`` inicial entre os clientes até o menor ``seq`` final. Clientes entram
    em instantes diferentes (a rampa garante isso) e comparar faixas desiguais
    acusaria divergência onde há apenas janelas distintas. Dentro da janela
    comum, três propriedades precisam valer:

    1. **contiguidade** - nenhum ``seq`` faltando entre o início e o fim;
    2. **unicidade** - nenhum ``seq`` repetido após o hold-back;
    3. **identidade** - a sequência de um cliente é igual à de todos os outros.

    Retorna o veredito e, se houver, as divergências com detalhe suficiente para
    investigação (sala, cliente, nó, o que faltou, o que sobrou).
    """
    por_sala: dict[str, list[ClientStats]] = defaultdict(list)
    for cliente in clientes:
        if cliente.conectou and len(cliente.seqs) > 0:
            por_sala[cliente.sala].append(cliente)

    divergencias: list[dict[str, Any]] = []
    total_divergencias = 0
    salas_ok = 0
    salas_sem_quorum: list[str] = []
    total_clientes = 0
    total_mensagens = 0
    chegadas_ordenadas = 0
    chegadas_fora_de_ordem = 0
    detalhes: list[dict[str, Any]] = []

    for sala in sorted(por_sala):
        participantes = por_sala[sala]
        total_clientes += len(participantes)

        renderizado: dict[int, list[int]] = {}
        for cliente in participantes:
            fora = _chegadas_fora_de_ordem(cliente.seqs)
            chegadas_fora_de_ordem += fora
            chegadas_ordenadas += fora == 0
            renderizado[cliente.indice] = _aplicar_hold_back(cliente.seqs)

        resultado = _verificar_sala(sala, participantes, renderizado)
        if resultado is None:
            salas_sem_quorum.append(sala)
            continue

        total_mensagens += resultado.mensagens
        total_divergencias += len(resultado.divergencias)
        salas_ok += not resultado.divergencias
        divergencias.extend(resultado.divergencias[: max(0, max_divergencias - len(divergencias))])
        detalhes.append(
            {
                "sala": resultado.sala,
                "clientes": resultado.clientes,
                "janela_comum": list(resultado.janela),
                "mensagens_verificadas": resultado.mensagens,
                "clientes_divergentes": len(resultado.divergencias),
            }
        )

    if not detalhes:
        veredito = "INCONCLUSIVO"
    elif total_divergencias == 0:
        veredito = "OK"
    else:
        veredito = "DIVERGENTE"

    return {
        "veredito": veredito,
        "salas_verificadas": len(detalhes),
        "salas_ok": salas_ok,
        "salas_sem_quorum": salas_sem_quorum,
        "clientes_verificados": total_clientes,
        "mensagens_verificadas": total_mensagens,
        "chegadas_fora_de_ordem": chegadas_fora_de_ordem,
        "clientes_com_chegada_ja_ordenada": chegadas_ordenadas,
        "divergencias_totais": total_divergencias,
        "divergencias": divergencias,
        "divergencias_omitidas": max(0, total_divergencias - len(divergencias)),
        "por_sala": detalhes,
    }


# --------------------------------------------------------------------------
# Limite de descritores de arquivo
# --------------------------------------------------------------------------


def ajustar_limite_descritores(necessario: int) -> dict[str, Any]:
    """Tenta elevar o limite flexível de descritores até o teto rígido.

    Cada conexão WebSocket consome um descritor. Com o limite padrão do macOS
    (256) um teste de 500 clientes falha por volta da conexão 250 — e o sintoma
    (``Too many open files``) é fácil de confundir com o servidor recusando
    conexões, que é justamente o que o teste deveria estar medindo.
    """
    if resource is None:  # pragma: no cover - Windows
        return {"suportado": False, "necessario": necessario}

    flexivel, rigido = resource.getrlimit(resource.RLIMIT_NOFILE)
    alvo = necessario + 256
    ajustado = flexivel
    if flexivel < alvo:
        teto = alvo if rigido == resource.RLIM_INFINITY else min(alvo, rigido)
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (teto, rigido))
            ajustado = teto
        except (ValueError, OSError):
            ajustado = flexivel

    return {
        "suportado": True,
        "necessario": necessario,
        "limite_inicial": flexivel,
        "limite_efetivo": ajustado,
        "limite_rigido": "ilimitado" if rigido == resource.RLIM_INFINITY else rigido,
        "suficiente": ajustado >= alvo,
    }


# --------------------------------------------------------------------------
# Executor
# --------------------------------------------------------------------------


class LoadRunner:
    """Orquestra a execução completa do teste de carga."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.clientes: list[ClientStats] = []

        self.t0 = 0.0
        self.t_fim_envio = 0.0
        self.encerrar = asyncio.Event()

        self.ativos = 0
        self.pico_ativos = 0
        self.resyncs_pedidos = 0
        """Quantos clientes precisaram recorrer ao histórico durável porque o
        Pub/Sub at-most-once não lhes entregou tudo (ADR-002)."""
        self.entregues = 0
        self.enviadas = 0

        self.entregues_por_s: Counter[int] = Counter()
        self.enviadas_por_s: Counter[int] = Counter()
        self.ativos_por_s: dict[int, int] = {}

        self.lat_ms: array = array("d")
        self.lat_bucket: array = array("i")

        self.falhas_conexao: Counter[str] = Counter()
        self.falhas_login: Counter[str] = Counter()
        self.limite_descritores: dict[str, Any] = {}

        self._conteudo = "x" * max(1, cfg.payload)
        self._aleatorio = random.Random(cfg.seed)

    # -- utilidades ------------------------------------------------------

    def _bucket(self) -> int:
        """Segundo (inteiro) decorrido desde o início da rampa."""
        return int(time.monotonic() - self.t0)

    def _log(self, mensagem: str) -> None:
        if not self.cfg.quiet:
            print(mensagem, file=sys.stderr, flush=True)

    # -- fase 1: autenticação -------------------------------------------

    def _login_sincrono(self, usuario: str) -> str:
        """Obtém um JWT em ``POST /auth/login`` (bloqueante, roda em thread)."""
        corpo = json.dumps({"username": usuario}).encode()
        requisicao = urlrequest.Request(  # noqa: S310 - esquema validado em Config
            f"{self.cfg.base_http}/auth/login",
            data=corpo,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlrequest.urlopen(requisicao, timeout=self.cfg.connect_timeout) as resposta:  # noqa: S310
            return str(json.loads(resposta.read())["token"])

    async def _autenticar_todos(self) -> None:
        """Autentica todos os clientes virtuais **antes** da rampa.

        Fazer o login dentro da rampa embutiria o round-trip HTTP na medição do
        handshake WebSocket e inflaria o percentil com um custo que não é do
        caminho medido.
        """
        limite = asyncio.Semaphore(self.cfg.login_concurrency)

        async def autenticar(cliente: ClientStats) -> None:
            async with limite:
                try:
                    cliente.token = await asyncio.to_thread(self._login_sincrono, cliente.usuario)
                except (urlerror.URLError, OSError, KeyError, ValueError) as exc:
                    cliente.erro = f"login: {type(exc).__name__}"
                    self.falhas_login[type(exc).__name__] += 1

        inicio = time.monotonic()
        await asyncio.gather(*(autenticar(c) for c in self.clientes))
        autenticados = sum(1 for c in self.clientes if c.token)
        self._log(
            f"[login] {autenticados}/{len(self.clientes)} tokens emitidos "
            f"em {time.monotonic() - inicio:.1f}s"
        )
        if autenticados == 0:
            raise SystemExit(
                f"nenhum login bem-sucedido em {self.cfg.base_http}/auth/login — "
                "o servidor está no ar?"
            )

    # -- fase 2: sessão de um cliente ------------------------------------

    async def _ler(self, ws: Any, cliente: ClientStats, entrou: asyncio.Event) -> None:
        """Consome frames do servidor até a conexão fechar.

        Roda como Task própria porque o cliente precisa ler continuamente: um
        gerador de carga que só lê entre envios acumularia atraso no buffer de
        recepção e mediria a própria lentidão como latência do servidor.
        """
        async for cru in ws:
            frame = json.loads(cru)
            tipo = frame.get("type")

            if tipo == "message":
                self._registrar_mensagem(cliente, frame)
            elif tipo == "ack":
                cliente.acks += 1
            elif tipo == "joined":
                if entrou.is_set():
                    # Reingresso após queda: o backlog é justamente o que o nó
                    # antigo não entregou. Registrá-lo é o que prova FR-8.
                    for envelope in frame.get("backlog", []):
                        self._registrar_seq(cliente, int(envelope["seq"]))
                else:
                    cliente.baseline_seq = int(frame.get("last_seq", 0))
                    cliente.ultimo_seq = cliente.baseline_seq
                    entrou.set()
            elif tipo == "error":
                cliente.erros_protocolo[str(frame.get("code", "desconhecido"))] += 1

    def _registrar_seq(self, cliente: ClientStats, seq: int) -> None:
        """Grava um ``seq`` na trilha do cliente, ignorando o histórico anterior."""
        if seq > cliente.baseline_seq:
            cliente.seqs.append(seq)
            cliente.ultimo_seq = max(cliente.ultimo_seq, seq)

    def _registrar_mensagem(self, cliente: ClientStats, frame: dict[str, Any]) -> None:
        """Contabiliza uma mensagem entregue e, se for eco próprio, a latência."""
        self._registrar_seq(cliente, int(frame["seq"]))

        self.entregues += 1
        self.entregues_por_s[self._bucket()] += 1

        enviado_em = cliente.em_voo.pop(str(frame.get("client_msg_id", "")), None)
        if enviado_em is not None:
            cliente.ecos += 1
            self.lat_ms.append((time.perf_counter() - enviado_em) * 1000.0)
            self.lat_bucket.append(self._bucket())

    async def _enviar(self, ws: Any, cliente: ClientStats) -> None:
        """Publica a uma taxa constante até o fim da janela de envio.

        O relógio de envio é acumulativo (``proximo += intervalo``) em vez de
        ``sleep(intervalo)`` a cada volta: dormir o intervalo cheio somaria o
        tempo de execução de cada iteração e faria a taxa real ficar abaixo da
        pedida — o erro clássico de gerador de carga.
        """
        intervalo = 1.0 / self.cfg.rate
        proximo = time.monotonic() + self._aleatorio.random() * intervalo

        while time.monotonic() < self.t_fim_envio:
            espera = proximo - time.monotonic()
            if espera > 0:
                await asyncio.sleep(espera)
            if time.monotonic() >= self.t_fim_envio:
                break

            identificador = uuid.uuid4().hex
            cliente.em_voo[identificador] = time.perf_counter()
            await ws.send(
                json.dumps(
                    {
                        "type": "send",
                        "room": cliente.sala,
                        "content": f"{cliente.usuario}#{cliente.enviadas} {self._conteudo}",
                        "client_msg_id": identificador,
                    }
                )
            )
            cliente.enviadas += 1
            self.enviadas += 1
            self.enviadas_por_s[self._bucket()] += 1

            proximo += intervalo
            agora = time.monotonic()
            if proximo < agora:
                # Ficamos para trás (servidor ou cliente saturado). Não tentamos
                # recuperar com rajada: isso amplificaria a saturação.
                proximo = agora + intervalo

    async def _sessao(self, cliente: ClientStats) -> None:
        """Uma conexão do handshake ao fechamento."""
        url = f"{self.cfg.base_ws}/ws?token={urlparse.quote(cliente.token or '')}"
        inicio = time.perf_counter()

        async with websockets.connect(
            url,
            open_timeout=self.cfg.connect_timeout,
            close_timeout=2,
            # O servidor mantém seu próprio heartbeat de aplicação a cada 20 s
            # (docs/protocolo.md); o ping do protocolo WebSocket seria tráfego
            # redundante multiplicado por milhares de conexões.
            ping_interval=None,
            # Sem compressão: o custo de CPU ficaria no gerador de carga e a
            # medição passaria a incluir o tempo de deflate do próprio teste.
            compression=None,
            max_queue=512,
        ) as ws:
            cliente.conexao_tcp_ms = (time.perf_counter() - inicio) * 1000.0

            bruto = await asyncio.wait_for(ws.recv(), timeout=self.cfg.connect_timeout)
            boas_vindas = json.loads(bruto)
            if boas_vindas.get("type") != "welcome":
                raise RuntimeError(f"primeiro frame inesperado: {boas_vindas.get('type')}")

            cliente.handshake_ms = (time.perf_counter() - inicio) * 1000.0
            cliente.node_id = boas_vindas.get("node_id")
            cliente.conectou = True
            cliente.sessoes += 1

            entrou = asyncio.Event()
            leitor = asyncio.create_task(self._ler(ws, cliente, entrou))
            try:
                await ws.send(
                    json.dumps(
                        {"type": "join", "room": cliente.sala, "last_seq": cliente.ultimo_seq}
                    )
                )
                await asyncio.wait_for(entrou.wait(), timeout=self.cfg.connect_timeout)

                self.ativos += 1
                self.pico_ativos = max(self.pico_ativos, self.ativos)
                try:
                    await self._trabalhar(ws, cliente, leitor)
                finally:
                    self.ativos -= 1
            finally:
                leitor.cancel()

    async def _reconciliar(self, ws: Any, cliente: ClientStats) -> None:
        """Pede ``resync`` se restou lacuna, como o cliente real faz.

        Redis Pub/Sub é *at-most-once* (ADR-002): sob carga, um assinante lento
        pode perder frames. O cliente real detecta a lacuna pela fila de
        hold-back e pede o trecho faltante ao histórico durável — é assim que a
        durabilidade é obtida sem pagar o custo de um canal durável.

        Sem este passo, o teste mediria um cliente que não existe: reportaria
        como perda do sistema aquilo que o cliente de verdade recupera. O que
        continua sendo medido separadamente é quanto o Pub/Sub perdeu — o campo
        ``chegadas_fora_de_ordem`` e a contagem de resyncs mostram o custo real
        da escolha.
        """
        if not len(cliente.seqs):
            return

        # A lacuna é definida exatamente como o verificador a define: falta de
        # contiguidade dentro do que o cliente renderizaria. Usar outra
        # definição aqui faria o resync não disparar justamente nos casos que o
        # relatório depois acusaria como divergência.
        renderizado = _aplicar_hold_back(cliente.seqs)
        primeiro, ultimo = renderizado[0], renderizado[-1]
        if len(renderizado) == ultimo - primeiro + 1:
            return  # contíguo, nada a recuperar

        contiguo_ate = primeiro - 1
        for seq in renderizado:
            if seq == contiguo_ate + 1:
                contiguo_ate = seq
            else:
                break

        self.resyncs_pedidos += 1
        try:
            await ws.send(
                json.dumps({"type": "resync", "room": cliente.sala, "after_seq": contiguo_ate})
            )
            # O leitor ainda está ativo e registra as mensagens que chegarem.
            await asyncio.sleep(self.cfg.resync_espera)
        except (OSError, WebSocketException):
            pass  # conexão já morreu: nada a recuperar por aqui

    async def _trabalhar(self, ws: Any, cliente: ClientStats, leitor: asyncio.Task) -> None:
        """Envia durante a janela e permanece lendo até o dreno terminar."""

        async def enviar_e_aguardar_fim() -> None:
            await self._enviar(ws, cliente)
            # Continua conectado durante o dreno: o eco de uma mensagem enviada
            # no último segundo ainda está em voo, e fechar agora contaria como
            # perda algo que é só latência.
            await self.encerrar.wait()
            await self._reconciliar(ws, cliente)

        trabalho = asyncio.create_task(enviar_e_aguardar_fim())
        concluidas, pendentes = await asyncio.wait(
            {leitor, trabalho}, return_when=asyncio.FIRST_COMPLETED
        )
        for tarefa in pendentes:
            tarefa.cancel()
        for tarefa in concluidas:
            if tarefa is trabalho and (exc := tarefa.exception()) is not None:
                raise exc

        # O leitor terminar antes do fim do teste significa que o servidor
        # fechou a conexão — inclusive quando fechou "educadamente", que é o
        # caso de um nó sendo derrubado. Sem esta conversão em erro, uma queda
        # se pareceria com um encerramento normal e sumiria do relatório
        # justamente na execução em que ela é o objeto do teste (critério EC3).
        if leitor in concluidas:
            falha = leitor.exception()
            if falha is not None:
                raise falha
            if not self.encerrar.is_set():
                raise ConnectionError("conexão encerrada pelo servidor durante o teste")

    async def _cliente(self, cliente: ClientStats, atraso: float) -> None:
        """Ciclo de vida completo de um cliente virtual, incluindo reconexão."""
        await asyncio.sleep(atraso)
        if cliente.token is None:
            return

        while not self.encerrar.is_set():
            sessoes_antes = cliente.sessoes
            try:
                await self._sessao(cliente)
                return
            except asyncio.CancelledError:
                raise
            except (WebSocketException, OSError, TimeoutError, RuntimeError) as exc:
                motivo = type(exc).__name__
                if cliente.sessoes > sessoes_antes:
                    # A sessão chegou a existir e caiu: é queda, não recusa.
                    cliente.quedas += 1
                elif cliente.sessoes == 0:
                    cliente.erro = motivo
                    self.falhas_conexao[motivo] += 1
                else:
                    cliente.reconexoes_falhas += 1

            if not self.cfg.reconnect or self.encerrar.is_set():
                return
            # Backoff com jitter: mil clientes reconectando em sincronia após a
            # queda de um nó criariam um pico artificial no nó sobrevivente.
            await asyncio.sleep(0.5 + self._aleatorio.random())

    # -- fase 3: amostragem ---------------------------------------------

    async def _amostrar(self) -> None:
        """Registra conexões ativas por segundo e imprime o progresso."""
        while not self.encerrar.is_set():
            await asyncio.sleep(1.0)
            segundo = self._bucket()
            self.ativos_por_s[segundo] = self.ativos
            if not self.cfg.quiet and segundo % 5 == 0:
                latencias = sorted(self.lat_ms[-2000:])
                p95 = percentil(latencias, 95) if latencias else 0.0
                self._log(
                    f"[{segundo:4d}s] conexões {self.ativos:5d}/{self.cfg.clients} · "
                    f"enviadas {self.enviadas:7d} · entregues {self.entregues:8d} · "
                    f"p95 {p95:6.1f} ms"
                )

    # -- orquestração ----------------------------------------------------

    async def run(self) -> dict[str, Any]:
        """Executa o teste completo e devolve o relatório."""
        cfg = self.cfg
        self.limite_descritores = ajustar_limite_descritores(cfg.clients)
        if self.limite_descritores.get("suportado") and not self.limite_descritores["suficiente"]:
            self._log(
                f"[aviso] limite de descritores é {self.limite_descritores['limite_efetivo']} "
                f"para {cfg.clients} conexões — veja 'ulimit -n' no README"
            )
        if cfg.rate > LIMITE_SERVIDOR_MSG_S:
            self._log(
                f"[aviso] --rate {cfg.rate} excede o rate limit do servidor "
                f"({LIMITE_SERVIDOR_MSG_S:.0f} msg/s por sessão): o teste medirá o rejeitador"
            )

        self.clientes = [
            ClientStats(
                indice=i,
                usuario=f"{cfg.user_prefix}-{i:05d}",
                sala=f"{cfg.room_prefix}-{i % cfg.rooms:03d}",
            )
            for i in range(cfg.clients)
        ]

        await self._autenticar_todos()

        inicio_iso = datetime.now(UTC).isoformat()
        inicio_relogio = time.monotonic()
        self.t0 = inicio_relogio
        self.t_fim_envio = self.t0 + cfg.ramp + cfg.duration

        amostrador = asyncio.create_task(self._amostrar())
        tarefas = [
            asyncio.create_task(
                self._cliente(cliente, (cliente.indice / max(1, cfg.clients)) * cfg.ramp)
            )
            for cliente in self.clientes
        ]

        self._log(
            f"[carga] rampa {cfg.ramp:.0f}s → janela {cfg.duration:.0f}s → dreno {cfg.drain:.0f}s"
        )
        await asyncio.sleep(cfg.ramp + cfg.duration)
        self._log(f"[dreno] aguardando {cfg.drain:.0f}s pelos ecos em voo")
        await asyncio.sleep(cfg.drain)

        self.encerrar.set()
        amostrador.cancel()
        await asyncio.gather(amostrador, *tarefas, return_exceptions=True)

        duracao_real = time.monotonic() - inicio_relogio
        return self._relatorio(inicio_iso, datetime.now(UTC).isoformat(), duracao_real)

    # -- relatório -------------------------------------------------------

    def _series_temporais(self) -> dict[str, list[float]]:
        """Séries por segundo, do primeiro ao último segundo observado."""
        segundos = set(self.entregues_por_s) | set(self.enviadas_por_s) | set(self.ativos_por_s)
        if not segundos:
            return {
                "t_s": [],
                "entregues_por_s": [],
                "enviadas_por_s": [],
                "conexoes_ativas": [],
                "latencia_p95_ms": [],
            }

        latencias_por_s: dict[int, list[float]] = defaultdict(list)
        for valor, segundo in zip(self.lat_ms, self.lat_bucket, strict=True):
            latencias_por_s[segundo].append(valor)

        eixo = list(range(0, max(segundos) + 1))
        return {
            "t_s": eixo,
            "entregues_por_s": [self.entregues_por_s.get(s, 0) for s in eixo],
            "enviadas_por_s": [self.enviadas_por_s.get(s, 0) for s in eixo],
            "conexoes_ativas": [self.ativos_por_s.get(s, 0) for s in eixo],
            "latencia_p95_ms": [
                round(percentil(sorted(latencias_por_s.get(s, [])), 95), 2) for s in eixo
            ],
        }

    def _relatorio(self, inicio: str, fim: str, duracao: float) -> dict[str, Any]:
        cfg = self.cfg
        corte_estavel = math.ceil(cfg.ramp)

        latencia_estavel = [
            valor
            for valor, segundo in zip(self.lat_ms, self.lat_bucket, strict=True)
            if segundo >= corte_estavel
        ]
        handshakes = [c.handshake_ms for c in self.clientes if c.conectou]
        conexoes_tcp = [c.conexao_tcp_ms for c in self.clientes if c.conectou]

        estabelecidas = sum(1 for c in self.clientes if c.conectou)
        sem_eco = sum(len(c.em_voo) for c in self.clientes)
        erros = Counter()
        for cliente in self.clientes:
            erros.update(cliente.erros_protocolo)

        entregues_estavel = sum(v for s, v in self.entregues_por_s.items() if s >= corte_estavel)
        enviadas_estavel = sum(v for s, v in self.enviadas_por_s.items() if s >= corte_estavel)
        segundos_estaveis = max(1.0, cfg.duration)

        ordem = verificar_ordem_total(self.clientes, cfg.max_divergencias)
        latencia = resumo_estatistico(latencia_estavel)
        handshake = resumo_estatistico(handshakes)

        return {
            "meta": {
                "ferramenta": "loadtest/run_load.py",
                "versao": "1.0.0",
                "inicio": inicio,
                "fim": fim,
                "duracao_real_s": round(duracao, 2),
                "python": platform.python_version(),
                "plataforma": platform.platform(),
                "parametros": {
                    "url": cfg.url,
                    "clients": cfg.clients,
                    "rooms": cfg.rooms,
                    "duration": cfg.duration,
                    "rate": cfg.rate,
                    "ramp": cfg.ramp,
                    "drain": cfg.drain,
                    "payload": cfg.payload,
                    "reconnect": cfg.reconnect,
                    "seed": cfg.seed,
                },
                "clientes_por_sala": round(cfg.clients / max(1, cfg.rooms), 1),
                "limite_descritores": self.limite_descritores,
            },
            "conexoes": {
                "solicitadas": cfg.clients,
                "estabelecidas": estabelecidas,
                "falhas": cfg.clients - estabelecidas,
                "taxa_sucesso_pct": round(100.0 * estabelecidas / max(1, cfg.clients), 2),
                "pico_simultaneas": self.pico_ativos,
                "resyncs_pedidos": self.resyncs_pedidos,
                "sessoes_abertas": sum(c.sessoes for c in self.clientes),
                "quedas_durante_o_teste": sum(c.quedas for c in self.clientes),
                "reconexoes_falhas": sum(c.reconexoes_falhas for c in self.clientes),
                "falhas_por_motivo": dict(self.falhas_conexao),
                "falhas_de_login": dict(self.falhas_login),
                "por_no": dict(
                    Counter(c.node_id for c in self.clientes if c.node_id).most_common()
                ),
            },
            "handshake_ms": handshake,
            "conexao_tcp_ms": resumo_estatistico(conexoes_tcp),
            "latencia_ms": latencia,
            "latencia_ms_execucao_completa": resumo_estatistico(list(self.lat_ms)),
            "mensagens": {
                "enviadas": self.enviadas,
                "ecos_recebidos": sum(c.ecos for c in self.clientes),
                "sem_eco_ao_final": sem_eco,
                "acks": sum(c.acks for c in self.clientes),
                "entregues_total": self.entregues,
                "erros_do_protocolo": dict(erros),
            },
            "throughput": {
                "publicadas_por_s": round(enviadas_estavel / segundos_estaveis, 1),
                "entregues_por_s": round(entregues_estavel / segundos_estaveis, 1),
                "pico_entregues_por_s": max(self.entregues_por_s.values(), default=0),
                "fan_out_medio": round(self.entregues / max(1, self.enviadas), 2),
                "janela": "estável (após a rampa)",
            },
            "series_temporais": self._series_temporais(),
            "ordem": ordem,
            "metas": {
                "latencia_p95_ms": {
                    "meta": META_P95_MS,
                    "medido": latencia["p95"],
                    "atingida": bool(latencia["amostras"] and latencia["p95"] < META_P95_MS),
                },
                "latencia_p99_ms": {
                    "meta": META_P99_MS,
                    "medido": latencia["p99"],
                    "atingida": bool(latencia["amostras"] and latencia["p99"] < META_P99_MS),
                },
                "handshake_p95_ms": {
                    "meta": META_HANDSHAKE_P95_MS,
                    "medido": handshake["p95"],
                    "atingida": bool(
                        handshake["amostras"] and handshake["p95"] < META_HANDSHAKE_P95_MS
                    ),
                },
                "ordem_total": {
                    "meta": "OK",
                    "medido": ordem["veredito"],
                    "atingida": ordem["veredito"] == "OK",
                },
            },
        }


# --------------------------------------------------------------------------
# Saída legível
# --------------------------------------------------------------------------


def imprimir_resumo(relatorio: dict[str, Any]) -> None:
    """Imprime o resumo em português, no formato usado na apresentação."""
    conexoes = relatorio["conexoes"]
    latencia = relatorio["latencia_ms"]
    handshake = relatorio["handshake_ms"]
    throughput = relatorio["throughput"]
    ordem = relatorio["ordem"]
    parametros = relatorio["meta"]["parametros"]

    linha = "─" * 68
    print(f"\n{linha}")
    print("  SalaViva — resultado do teste de carga")
    print(linha)
    print(f"  Alvo ................. {parametros['url']}")
    print(
        f"  Carga ................ {parametros['clients']} clientes · "
        f"{parametros['rooms']} salas · {parametros['rate']} msg/s por cliente"
    )
    print(
        f"  Conexões ............. {conexoes['estabelecidas']}/{conexoes['solicitadas']} "
        f"({conexoes['taxa_sucesso_pct']}%) · pico simultâneo {conexoes['pico_simultaneas']}"
    )
    if conexoes["falhas_por_motivo"]:
        print(f"  Falhas ............... {conexoes['falhas_por_motivo']}")
    if conexoes["quedas_durante_o_teste"] or conexoes["reconexoes_falhas"]:
        print(
            f"  Quedas ............... {conexoes['quedas_durante_o_teste']} sessões caíram · "
            f"{conexoes['reconexoes_falhas']} reconexões recusadas · "
            f"{conexoes['sessoes_abertas']} sessões abertas no total"
        )
    if conexoes["por_no"]:
        print(f"  Distribuição por nó .. {conexoes['por_no']}")
    print(linha)
    print(
        f"  Latência fim a fim ... p50 {latencia['p50']:.1f} ms · "
        f"p95 {latencia['p95']:.1f} ms · p99 {latencia['p99']:.1f} ms · "
        f"máx {latencia['max']:.1f} ms"
    )
    print(f"  Handshake ............ p95 {handshake['p95']:.1f} ms")
    print(
        f"  Throughput ........... {throughput['entregues_por_s']:.0f} msg/s entregues "
        f"({throughput['publicadas_por_s']:.0f} publicadas/s · "
        f"fan-out {throughput['fan_out_medio']}x)"
    )
    print(linha)
    print(f"  ORDEM TOTAL: {ordem['veredito']}")
    print(
        f"  {ordem['salas_ok']}/{ordem['salas_verificadas']} salas íntegras · "
        f"{ordem['clientes_verificados']} clientes · "
        f"{ordem['mensagens_verificadas']} mensagens verificadas"
    )
    print(
        f"  Chegadas fora de ordem no Pub/Sub: {ordem['chegadas_fora_de_ordem']} "
        "(corrigidas pela fila de hold-back)"
    )
    for divergencia in ordem["divergencias"]:
        print(
            f"    ! sala {divergencia['sala']} · cliente {divergencia['cliente']} · "
            f"{divergencia['tipo']} · faltando {divergencia['seq_faltando']} "
            f"· excedente {divergencia['seq_excedente']}"
        )
    print(linha)
    for nome, alvo in relatorio["metas"].items():
        marcador = "OK  " if alvo["atingida"] else "FALHA"
        print(f"  [{marcador}] {nome}: medido {alvo['medido']} · meta {alvo['meta']}")
    print(f"{linha}\n")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _argumentos(argv: list[str] | None = None) -> Config:
    analisador = argparse.ArgumentParser(
        prog="python -m loadtest.run_load",
        description="Teste de carga e verificação de ordem total do SalaViva.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  # cluster local (Docker Compose)\n"
            "  python -m loadtest.run_load --url http://localhost:8080 --clients 300\n\n"
            "  # AWS, alvo de 1.000+ conexões\n"
            "  python -m loadtest.run_load --url http://SEU-ALB.us-east-1.elb.amazonaws.com \\\n"
            "      --clients 1200 --rooms 20 --rate 0.5 --ramp 60 --duration 120\n"
        ),
    )
    analisador.add_argument(
        "--url",
        default="http://localhost:8000",
        help="URL base do serviço (http/https/ws/wss). Padrão: %(default)s",
    )
    analisador.add_argument(
        "--clients",
        type=int,
        default=500,
        help="conexões WebSocket concorrentes. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--rooms",
        type=int,
        default=20,
        help="salas entre as quais distribuir os clientes. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="janela estável de medição, em segundos. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--rate",
        type=float,
        default=0.5,
        help="mensagens por segundo por cliente. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--ramp",
        type=float,
        default=30.0,
        help="rampa de subida das conexões, em segundos. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "resultado.json",
        help="arquivo JSON de resultado. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--drain",
        type=float,
        default=5.0,
        help="segundos de escuta após o último envio. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--payload",
        type=int,
        default=64,
        help="bytes de preenchimento por mensagem. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--room-prefix", default="carga", help="prefixo dos nomes de sala. Padrão: %(default)s"
    )
    analisador.add_argument(
        "--user-prefix", default="carga", help="prefixo dos nomes de usuário. Padrão: %(default)s"
    )
    analisador.add_argument(
        "--reconnect",
        action="store_true",
        help="reconectar e refazer o join ao cair (use junto com a demonstração de falha)",
    )
    analisador.add_argument(
        "--connect-timeout",
        type=float,
        default=20.0,
        help="tempo limite de handshake, em segundos. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--login-concurrency",
        type=int,
        default=32,
        help="logins HTTP simultâneos na preparação. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--max-divergencias",
        type=int,
        default=20,
        help="divergências de ordem detalhadas. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--seed",
        type=int,
        default=42,
        help="semente do jitter, para reprodutibilidade. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--quiet", action="store_true", help="silencia o progresso (mantém o resumo final)"
    )

    args = analisador.parse_args(argv)
    if args.clients < 1 or args.rooms < 1 or args.rate <= 0:
        analisador.error("--clients e --rooms devem ser ≥ 1 e --rate deve ser > 0")

    return Config(
        url=args.url,
        clients=args.clients,
        rooms=args.rooms,
        duration=args.duration,
        rate=args.rate,
        ramp=args.ramp,
        out=args.out,
        drain=args.drain,
        payload=args.payload,
        room_prefix=args.room_prefix,
        user_prefix=args.user_prefix,
        reconnect=args.reconnect,
        connect_timeout=args.connect_timeout,
        login_concurrency=args.login_concurrency,
        max_divergencias=args.max_divergencias,
        seed=args.seed,
        quiet=args.quiet,
    )


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada. Devolve 0 se todas as metas foram atingidas."""
    cfg = _argumentos(argv)
    relatorio = asyncio.run(LoadRunner(cfg).run())

    cfg.out.parent.mkdir(parents=True, exist_ok=True)
    cfg.out.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")

    imprimir_resumo(relatorio)
    print(f"Resultado salvo em {cfg.out}")
    print(f"Gráficos:  python -m loadtest.plot_results --json {cfg.out}")

    # Código de saída diferente de zero permite usar o teste em CI: a violação
    # da ordem total é uma falha de correção, não um número ruim.
    return 0 if all(m["atingida"] for m in relatorio["metas"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
