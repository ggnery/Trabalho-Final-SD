"""Caso de uso central do chat.

Orquestra o núcleo puro (relógios, envelope) e as portas (barramento,
sequenciador, repositório, presença) para realizar as duas operações que
definem o sistema: **publicar** uma mensagem e **entregar** o que chega do
barramento.

O caminho crítico de uma mensagem, na ordem exata em que acontece:

1. ``dedupe`` — ``SET NX`` no ``client_msg_id`` (idempotência)
2. ``lamport.tick()`` — relógio lógico do nó avança
3. ``vclock.tick()`` — relógio vetorial avança
4. ``sequencer.next_seq()`` — **ordem total é decidida aqui**, atomicamente
5. ``bus.publish()`` — difusão para todo o cluster
6. ``repository.save()`` — persistência, em Task paralela (ADR-008)

A mensagem **não** é entregue localmente por atalho: ela volta pelo Pub/Sub e
segue o mesmo caminho de qualquer outra (ADR-004). O ``ack`` devolvido no passo
4 é o que mantém a UI responsiva apesar disso.
"""

from __future__ import annotations

import asyncio
import time

import structlog

from salaviva.config import Settings
from salaviva.domain.clocks import LamportClock, VectorClock
from salaviva.domain.errors import MessageTooLong
from salaviva.domain.models import Member, MessageEnvelope, NodeInfo, RoomSummary, utc_now_iso
from salaviva.ports import (
    IdempotencyStore,
    MessageBus,
    MessageRepository,
    NodeRegistry,
    PresenceStore,
    Sequencer,
)
from salaviva.ws.manager import ClientConnection, ConnectionManager
from salaviva.ws.protocol import Ack, JoinedFrame, LeftFrame, PresenceUpdate

__all__ = ["ChatService"]

log = structlog.get_logger(__name__)

_PENDING = "pending"
"""Marcador provisório da reivindicação de idempotência, antes de o ``seq``
ser conhecido. Um reenvio que encontre este valor está competindo com o envio
original ainda em voo."""


def _duplicate_ack(client_msg_id: str, room_id: str, claimed: str) -> Ack:
    """Monta o ``ack`` de um reenvio detectado como duplicata.

    Se o valor reivindicado ainda é o marcador provisório, o envio original não
    concluiu e o ``seq`` é desconhecido; devolvemos ``seq=0`` e o cliente
    reconcilia quando o eco da mensagem chegar pelo Pub/Sub.
    """
    if claimed == _PENDING:
        return Ack(client_msg_id=client_msg_id, room=room_id, seq=0, lamport=0, duplicate=True)
    seq_str, _, lamport_str = claimed.partition(":")
    return Ack(
        client_msg_id=client_msg_id,
        room=room_id,
        seq=int(seq_str or 0),
        lamport=int(lamport_str or 0),
        duplicate=True,
    )


class ChatService:
    """Serviço de chat de um nó."""

    def __init__(
        self,
        *,
        settings: Settings,
        manager: ConnectionManager,
        bus: MessageBus,
        sequencer: Sequencer,
        repository: MessageRepository,
        presence: PresenceStore,
        idempotency: IdempotencyStore,
        node_registry: NodeRegistry,
    ) -> None:
        self.settings = settings
        self.node_id = settings.node_id
        self.manager = manager
        self.bus = bus
        self.sequencer = sequencer
        self.repository = repository
        self.presence = presence
        self.idempotency = idempotency
        self.node_registry = node_registry

        self.lamport = LamportClock(self.node_id)
        self.vclock = VectorClock(self.node_id)

        self._started_at = time.monotonic()
        self._background: list[asyncio.Task] = []
        self._pending_saves: set[asyncio.Task] = set()

        # Métricas expostas em /metrics e no painel de nós.
        self.messages_published = 0
        self.messages_delivered = 0
        self.messages_deduplicated = 0

    # -- Ciclo de vida -----------------------------------------------------

    async def start(self) -> None:
        self.bus.on_message(self._on_bus_message)
        await self.bus.start()
        self._background = [
            asyncio.create_task(self._sweeper_loop(), name="sweeper"),
            asyncio.create_task(self._node_heartbeat_loop(), name="node-heartbeat"),
        ]
        log.info("chat_service_iniciado", node_id=self.node_id)

    async def stop(self) -> None:
        for task in self._background:
            task.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)
        if self._pending_saves:
            await asyncio.gather(*self._pending_saves, return_exceptions=True)
        await self.bus.stop()
        log.info("chat_service_encerrado", node_id=self.node_id)

    # -- Operações do cliente ---------------------------------------------

    async def join(self, conn: ClientConnection, room_id: str, last_seq: int = 0) -> JoinedFrame:
        """Entra em uma sala e devolve membros + backlog perdido."""
        first_local = self.manager.join_room(conn, room_id)
        if first_local:
            # Só assina o tópico quando o primeiro membro local chega. Um nó não
            # deve receber o tráfego de salas em que não tem ninguém.
            await self.bus.subscribe(room_id)

        await self.presence.join(room_id, conn.member)

        # A partir daqui o nó já está inscrito no tópico, então tudo que for
        # publicado de agora em diante chega ao vivo. O backlog precisa cobrir
        # exatamente a faixa (last_seq, current_seq] — e é aí que mora a corrida
        # descrita abaixo.
        current_seq = await self.sequencer.current(room_id)
        backlog = await self._backlog_contiguo(room_id, last_seq, current_seq)
        members = await self.presence.members(room_id)

        # Quem acabou de entrar não recebe o presence_update: a lista completa
        # já vai no frame `joined`. Difundir para ele criaria um frame que chega
        # antes do `joined`, com informação que o `joined` repete.
        await self._broadcast_presence(room_id, "join", conn.user, members, exclude=conn.session_id)

        log.info(
            "sala_ingressada",
            node_id=self.node_id,
            room_id=room_id,
            user=conn.user,
            backlog=len(backlog),
            last_seq=last_seq,
        )
        return JoinedFrame(
            room=room_id,
            members=members,
            backlog=backlog,
            last_seq=current_seq,
            node_id=self.node_id,
        )

    async def leave(self, conn: ClientConnection, room_id: str) -> LeftFrame:
        emptied = self.manager.leave_room(conn, room_id)
        await self.presence.leave(room_id, conn.session_id)
        if emptied:
            await self.bus.unsubscribe(room_id)
        members = await self.presence.members(room_id)
        await self._broadcast_presence(room_id, "leave", conn.user, members)
        return LeftFrame(room=room_id)

    async def send(
        self,
        conn: ClientConnection,
        room_id: str,
        content: str,
        client_msg_id: str,
    ) -> Ack:
        """Publica uma mensagem. Devolve o ``ack`` com o ``seq`` atribuído."""
        if len(content) > self.settings.max_content_length:
            raise MessageTooLong

        # 1. Idempotência. A reivindicação grava um marcador provisório; o
        #    resultado real substitui esse marcador assim que o seq é conhecido.
        #    Um reenvio devolve o ack original, sem consumir um novo seq.
        claimed = await self.idempotency.claim(
            client_msg_id, _PENDING, ttl_seconds=self.settings.idempotency_ttl
        )
        if claimed is not None:
            self.messages_deduplicated += 1
            return _duplicate_ack(client_msg_id, room_id, claimed)

        # 2-3. Relógios lógicos avançam antes da difusão.
        lamport = self.lamport.tick()
        vector = self.vclock.tick()

        # 4. Ordem total é decidida aqui, e em nenhum outro lugar.
        seq = await self.sequencer.next_seq(room_id)

        envelope = MessageEnvelope(
            client_msg_id=client_msg_id,
            room_id=room_id,
            sender=conn.user,
            session_id=conn.session_id,
            content=content,
            seq=seq,
            lamport=lamport,
            vector_clock=vector,
            node_id=self.node_id,
        )

        # Substitui o marcador provisório pelo resultado, para que um reenvio
        # posterior receba o mesmo seq em vez de um novo.
        await self.idempotency.record(
            client_msg_id,
            f"{seq}:{lamport}",
            ttl_seconds=self.settings.idempotency_ttl,
        )

        # 5. Difusão. O emissor receberá esta mensagem de volta (ADR-004).
        await self.bus.publish(room_id, envelope)
        self.messages_published += 1

        # 6. Persistência fora do caminho crítico (ADR-008).
        self._save_async(envelope)

        log.info(
            "mensagem_publicada",
            node_id=self.node_id,
            room_id=room_id,
            seq=seq,
            lamport=lamport,
            sender=conn.user,
        )
        return Ack(client_msg_id=client_msg_id, room=room_id, seq=seq, lamport=lamport)

    async def _backlog_contiguo(
        self, room_id: str, last_seq: int, current_seq: int
    ) -> list[MessageEnvelope]:
        """Lê o backlog garantindo contiguidade até ``current_seq``.

        Fecha uma corrida real, observada em teste de carga: a persistência é
        assíncrona (ADR-008), então existe uma janela de alguns milissegundos em
        que uma mensagem **já foi publicada mas ainda não foi gravada**. Um
        cliente que assina o tópico dentro dessa janela não recebe a mensagem ao
        vivo (ela foi publicada antes da inscrição) *e* não a encontra no
        backlog (ainda não foi gravada) — resultando em uma lacuna permanente.

        A correção é barata porque sabemos exatamente quais números de sequência
        deveriam estar presentes: a faixa ``(last_seq, current_seq]``. Se algum
        falta, esperamos alguns milissegundos e lemos de novo. O custo só existe
        quando a corrida de fato acontece; no caminho normal, uma única leitura
        basta e a função retorna sem dormir.

        A tentativa é limitada: se a lacuna persistir (mensagem que nunca será
        gravada porque o nó de origem morreu antes), devolvemos o que existe e o
        cliente sinaliza a lacuna em vez de esperar indefinidamente.
        """
        limite = self.settings.backlog_limit
        backlog = await self.repository.backlog(room_id, after_seq=last_seq, limit=limite)

        if not self.settings.persistence_enabled or current_seq <= last_seq:
            return backlog

        # Só exigimos contiguidade dentro da janela que o limite comporta.
        esperado_ate = min(current_seq, last_seq + limite)

        for tentativa in range(1, 4):
            presentes = {m.seq for m in backlog}
            faltando = [s for s in range(last_seq + 1, esperado_ate + 1) if s not in presentes]
            if not faltando:
                return backlog

            await asyncio.sleep(0.04 * tentativa)
            backlog = await self.repository.backlog(room_id, after_seq=last_seq, limit=limite)
            log.info(
                "backlog_reconsultado",
                node_id=self.node_id,
                room_id=room_id,
                tentativa=tentativa,
                faltando=len(faltando),
            )

        return backlog

    async def resync(
        self, conn: ClientConnection, room_id: str, after_seq: int
    ) -> list[MessageEnvelope]:
        """Reenvia o backlog a partir de ``after_seq``.

        Acionado pelo cliente quando a fila de hold-back detecta uma lacuna que
        não se fecha — o caso em que o Pub/Sub *at-most-once* perdeu a mensagem
        para este nó específico.
        """
        backlog = await self.repository.backlog(
            room_id, after_seq=after_seq, limit=self.settings.backlog_limit
        )
        log.info(
            "resync_atendido",
            node_id=self.node_id,
            room_id=room_id,
            after_seq=after_seq,
            recovered=len(backlog),
        )
        return backlog

    async def disconnect(self, conn: ClientConnection) -> None:
        """Limpa todo o estado de uma sessão encerrada."""
        rooms = list(conn.rooms)
        emptied = self.manager.remove(conn.session_id)
        for room_id in rooms:
            await self.presence.leave(room_id, conn.session_id)
            members = await self.presence.members(room_id)
            await self._broadcast_presence(room_id, "leave", conn.user, members)
        for room_id in emptied:
            await self.bus.unsubscribe(room_id)

    async def heartbeat(self, conn: ClientConnection) -> None:
        for room_id in conn.rooms:
            await self.presence.heartbeat(room_id, conn.session_id)

    # -- Entrega vinda do barramento --------------------------------------

    async def _on_bus_message(self, room_id: str, envelope: MessageEnvelope) -> None:
        """Chamado para toda mensagem que chega do Pub/Sub.

        Inclusive as originadas neste próprio nó (ADR-004) — este é o **único**
        caminho de entrega do sistema, o que faz a ordem observada ser idêntica
        para todos os clientes por construção, e não por coincidência.
        """
        # Regra 2 de Lamport: o recebimento avança o relógio local.
        self.lamport.update(envelope.lamport)
        self.vclock.merge(envelope.vector_clock)

        delivered = await self.manager.broadcast(room_id, envelope.to_wire())
        self.messages_delivered += delivered

    # -- Tarefas de fundo --------------------------------------------------

    def _save_async(self, envelope: MessageEnvelope) -> None:
        """Persiste em Task paralela, mantendo referência forte.

        A referência em ``_pending_saves`` evita que o garbage collector recolha
        a Task antes de ela concluir — uma armadilha conhecida do ``asyncio``,
        em que a escrita desaparece silenciosamente sob carga.
        """
        task = asyncio.create_task(self.repository.save(envelope))
        self._pending_saves.add(task)
        task.add_done_callback(self._pending_saves.discard)

    async def _sweeper_loop(self) -> None:
        """Remove presenças e nós cujo heartbeat expirou.

        Roda em todos os nós, de propósito: eleger um varredor único criaria um
        ponto de falha cuja morte deixaria membros fantasmas permanentes. As
        operações são idempotentes, então a redundância não custa correção.
        """
        while True:
            try:
                await asyncio.sleep(self.settings.sweeper_interval)
                removed = await self.presence.sweep(self.settings.presence_ttl)
                await self.node_registry.sweep(self.settings.presence_ttl)
                if removed:
                    log.info("presenca_varrida", node_id=self.node_id, removed=removed)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("erro_no_sweeper", node_id=self.node_id)

    async def _node_heartbeat_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.settings.node_heartbeat_interval)
                await self.node_registry.heartbeat(self.node_info())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("erro_no_heartbeat_do_no", node_id=self.node_id)

    # -- Consultas ---------------------------------------------------------

    async def rooms(self) -> list[RoomSummary]:
        return await self.presence.rooms()

    async def alive_nodes(self) -> list[NodeInfo]:
        return await self.node_registry.alive(self.settings.presence_ttl)

    def node_info(self) -> NodeInfo:
        return NodeInfo(
            node_id=self.node_id,
            connections=self.manager.connection_count,
            rooms=self.manager.room_count,
            messages_published=self.messages_published,
            messages_delivered=self.messages_delivered,
            lamport=self.lamport.value,
            uptime_seconds=round(time.monotonic() - self._started_at, 1),
            last_heartbeat=utc_now_iso(),
        )

    def metrics(self) -> dict:
        info = self.node_info()
        return {
            **info.model_dump(),
            "messages_deduplicated": self.messages_deduplicated,
            "vector_clock": self.vclock.snapshot(),
            "local_rooms": self.manager.local_rooms(),
        }

    # -- Auxiliares --------------------------------------------------------

    async def _broadcast_presence(
        self,
        room_id: str,
        event: str,
        user: str,
        members: list[Member],
        *,
        exclude: str | None = None,
    ) -> None:
        """Difunde a mudança de presença aos membros **locais** da sala.

        Presença não passa pelo Pub/Sub: cada nó lê o estado compartilhado do
        Redis e informa os próprios clientes. Difundir presença pelo barramento
        multiplicaria o tráfego sem ganho — o dado já está em um store que todos
        os nós enxergam.
        """
        frame = PresenceUpdate(
            room=room_id,
            event=event,  # type: ignore[arg-type]
            user=user,
            members=members,
        )
        await self.manager.broadcast(room_id, frame.model_dump(), exclude=exclude)
