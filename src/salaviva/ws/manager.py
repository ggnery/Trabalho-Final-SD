"""Conexões WebSocket locais de um nó.

O :class:`ConnectionManager` conhece **apenas** as conexões deste nó. Ele não
sabe — e não precisa saber — quantos clientes existem no cluster nem em quais
outros nós estão. A difusão para o resto do cluster é responsabilidade do
barramento Pub/Sub; aqui só acontece a entrega local.

Essa separação é o que permite ao sistema escalar horizontalmente sem que
nenhum componente mantenha uma visão global do conjunto de conexões.
"""

from __future__ import annotations

import asyncio
import contextlib

import structlog
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from salaviva.domain.models import Member

__all__ = ["ClientConnection", "ConnectionManager"]

log = structlog.get_logger(__name__)


class ClientConnection:
    """Uma sessão WebSocket."""

    __slots__ = ("_send_lock", "member", "rooms", "session_id", "user", "websocket")

    def __init__(self, websocket: WebSocket, session_id: str, user: str, node_id: str) -> None:
        self.websocket = websocket
        self.session_id = session_id
        self.user = user
        self.rooms: set[str] = set()
        self.member = Member(user=user, session_id=session_id, node_id=node_id)
        # Serializa os envios: várias corrotinas (entrega de sala A, de sala B,
        # heartbeat) podem tentar escrever no mesmo socket ao mesmo tempo, e
        # frames WebSocket entrelaçados corrompem a conexão.
        self._send_lock = asyncio.Lock()

    async def send_json(self, payload: dict) -> bool:
        """Envia um frame. Devolve ``False`` se a conexão já não serve.

        Nunca propaga exceção: uma conexão morta é um evento esperado (o usuário
        fechou a aba, a rede caiu), não um erro que deva interromper a difusão
        para os demais membros da sala.
        """
        if self.websocket.client_state is not WebSocketState.CONNECTED:
            return False
        try:
            async with self._send_lock:
                await self.websocket.send_json(payload)
            return True
        except (RuntimeError, ConnectionError, asyncio.CancelledError):
            return False
        except Exception:  # noqa: BLE001
            log.debug("falha_envio_ws", session_id=self.session_id)
            return False

    async def close(self, code: int = 1000, reason: str = "") -> None:
        with contextlib.suppress(RuntimeError, ConnectionError):
            await self.websocket.close(code=code, reason=reason)


class ConnectionManager:
    """Índice de conexões locais, por sessão e por sala."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self._by_session: dict[str, ClientConnection] = {}
        self._by_room: dict[str, set[str]] = {}

    # -- Ciclo de vida da conexão -----------------------------------------

    def add(self, conn: ClientConnection) -> None:
        self._by_session[conn.session_id] = conn

    def remove(self, session_id: str) -> list[str]:
        """Remove a sessão e devolve as salas que ficaram sem membro local.

        O chamador usa essa lista para cancelar as assinaturas Pub/Sub
        correspondentes — um nó não deve continuar recebendo o tráfego de uma
        sala em que não tem mais ninguém.
        """
        conn = self._by_session.pop(session_id, None)
        if conn is None:
            return []
        emptied: list[str] = []
        for room in conn.rooms:
            members = self._by_room.get(room)
            if members is None:
                continue
            members.discard(session_id)
            if not members:
                del self._by_room[room]
                emptied.append(room)
        return emptied

    def get(self, session_id: str) -> ClientConnection | None:
        return self._by_session.get(session_id)

    # -- Salas -------------------------------------------------------------

    def join_room(self, conn: ClientConnection, room_id: str) -> bool:
        """Vincula a sessão à sala. Devolve ``True`` se é o primeiro membro local."""
        first = room_id not in self._by_room
        self._by_room.setdefault(room_id, set()).add(conn.session_id)
        conn.rooms.add(room_id)
        return first

    def leave_room(self, conn: ClientConnection, room_id: str) -> bool:
        """Desvincula. Devolve ``True`` se a sala ficou sem membro local."""
        conn.rooms.discard(room_id)
        members = self._by_room.get(room_id)
        if members is None:
            return False
        members.discard(conn.session_id)
        if not members:
            del self._by_room[room_id]
            return True
        return False

    # -- Entrega -----------------------------------------------------------

    async def broadcast(self, room_id: str, payload: dict, *, exclude: str | None = None) -> int:
        """Entrega ``payload`` aos membros locais da sala. Devolve quantos receberam.

        Os envios acontecem concorrentemente: com centenas de membros em uma
        sala, entregar em série faria o último cliente esperar a soma de todas
        as escritas anteriores.
        """
        session_ids = self._by_room.get(room_id)
        if not session_ids:
            return 0

        targets = [
            conn
            for sid in list(session_ids)
            if sid != exclude and (conn := self._by_session.get(sid)) is not None
        ]
        if not targets:
            return 0

        results = await asyncio.gather(
            *(c.send_json(payload) for c in targets), return_exceptions=True
        )
        return sum(1 for r in results if r is True)

    async def send_to(self, session_id: str, payload: dict) -> bool:
        conn = self._by_session.get(session_id)
        return await conn.send_json(payload) if conn else False

    # -- Métricas ----------------------------------------------------------

    @property
    def connection_count(self) -> int:
        return len(self._by_session)

    @property
    def room_count(self) -> int:
        return len(self._by_room)

    def local_rooms(self) -> list[str]:
        return list(self._by_room)

    def all_connections(self) -> list[ClientConnection]:
        return list(self._by_session.values())
