"""Laço de atendimento de uma conexão WebSocket.

Cada conexão é atendida por sua própria ``asyncio.Task``, com tratamento de erro
no topo. A consequência é a propriedade que o sistema precisa ter: **uma conexão
que falha não derruba as outras**. Um cliente que envia lixo, estoura o rate
limit ou some da rede afeta apenas a si mesmo.

O ping periódico serve a dois propósitos ao mesmo tempo: detectar conexões
mortas (TCP pode demorar minutos para perceber) e renovar a presença do usuário
nas salas. Unificar as duas coisas em um só temporizador evita que um cliente
vivo, porém silencioso, seja varrido da lista de presença.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import structlog
from fastapi import WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from salaviva.api.auth import decode_token
from salaviva.app.chat_service import ChatService
from salaviva.app.rate_limit import TokenBucket
from salaviva.config import Settings
from salaviva.domain.errors import AuthenticationFailed, RateLimitExceeded, SalaVivaError
from salaviva.domain.models import utc_now_iso
from salaviva.ws.manager import ClientConnection
from salaviva.ws.protocol import (
    ErrorFrame,
    JoinCommand,
    LeaveCommand,
    PingCommand,
    PongFrame,
    ResyncCommand,
    SendCommand,
    TypingCommand,
    TypingFrame,
    WelcomeFrame,
    parse_client_command,
)

__all__ = ["serve_websocket"]

log = structlog.get_logger(__name__)


async def serve_websocket(
    websocket: WebSocket,
    token: str,
    service: ChatService,
    settings: Settings,
) -> None:
    """Atende uma conexão do início ao fim."""
    # Autenticação acontece ANTES do accept: um token inválido nunca chega a
    # consumir um slot de conexão no nó.
    try:
        user = decode_token(token, settings)
    except AuthenticationFailed as exc:
        await websocket.close(code=exc.ws_close_code, reason=exc.code)
        log.info("handshake_rejeitado", node_id=service.node_id, reason=exc.code)
        return

    await websocket.accept()

    session_id = uuid.uuid4().hex
    conn = ClientConnection(websocket, session_id, user, service.node_id)
    service.manager.add(conn)
    bucket = TokenBucket(settings.rate_limit_per_second, settings.rate_limit_burst)

    heartbeat = asyncio.create_task(
        _heartbeat_loop(conn, service, settings), name=f"hb-{session_id[:8]}"
    )

    log.info("sessao_aberta", node_id=service.node_id, user=user, session_id=session_id)

    try:
        await conn.send_json(
            WelcomeFrame(
                node_id=service.node_id,
                session_id=session_id,
                user=user,
                server_time=utc_now_iso(),
            ).model_dump()
        )
        await _receive_loop(conn, service, settings, bucket)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("erro_na_sessao", node_id=service.node_id, session_id=session_id)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        await service.disconnect(conn)
        log.info("sessao_encerrada", node_id=service.node_id, user=user, session_id=session_id)


async def _receive_loop(
    conn: ClientConnection,
    service: ChatService,
    settings: Settings,
    bucket: TokenBucket,
) -> None:
    while True:
        raw = await conn.websocket.receive_text()

        try:
            command = parse_client_command(raw)
        except ValidationError as exc:
            await conn.send_json(
                ErrorFrame(
                    code="invalid_message",
                    message="frame não corresponde ao protocolo",
                    detail={"errors": exc.error_count()},
                ).model_dump()
            )
            continue

        try:
            await _dispatch(command, conn, service, settings, bucket)
        except SalaVivaError as exc:
            # Erro de domínio é resposta, não queda: o cliente recebe um código
            # estável e decide o que fazer (backoff, reautenticar, encurtar).
            await conn.send_json(exc.to_wire())
        except (ConnectionError, RuntimeError) as exc:
            # Dependência caiu no meio da operação. A conexão do usuário
            # sobrevive; /readyz é que vai reprovar e tirar o nó do pool.
            log.warning(
                "dependencia_indisponivel",
                node_id=service.node_id,
                session_id=conn.session_id,
                error=str(exc),
            )
            await conn.send_json(
                ErrorFrame(
                    code="service_unavailable",
                    message="nó temporariamente degradado, tente novamente",
                ).model_dump()
            )


async def _dispatch(
    command: JoinCommand | LeaveCommand | SendCommand | ResyncCommand | TypingCommand | PingCommand,
    conn: ClientConnection,
    service: ChatService,
    settings: Settings,
    bucket: TokenBucket,
) -> None:
    match command:
        case JoinCommand():
            frame = await service.join(conn, command.room, command.last_seq)
            await conn.send_json(frame.model_dump())

        case LeaveCommand():
            frame = await service.leave(conn, command.room)
            await conn.send_json(frame.model_dump())

        case SendCommand():
            if command.room not in conn.rooms:
                # Publicar em sala não ocupada seria uma forma de contornar a
                # presença e enviar mensagem anonimamente.
                await conn.send_json(
                    ErrorFrame(
                        code="not_in_room",
                        message=f"entre na sala '{command.room}' antes de enviar",
                    ).model_dump()
                )
                return
            if not bucket.allow():
                raise RateLimitExceeded(
                    f"limite de {settings.rate_limit_per_second:.0f} msg/s excedido; "
                    f"aguarde {bucket.retry_after():.1f}s"
                )
            ack = await service.send(conn, command.room, command.content, command.client_msg_id)
            await conn.send_json(ack.model_dump())

        case ResyncCommand():
            backlog = await service.resync(conn, command.room, command.after_seq)
            for envelope in backlog:
                await conn.send_json(envelope.to_wire())

        case TypingCommand():
            if command.room in conn.rooms:
                await service.manager.broadcast(
                    command.room,
                    TypingFrame(room=command.room, user=conn.user).model_dump(),
                    exclude=conn.session_id,
                )

        case PingCommand():
            await conn.send_json(
                PongFrame(server_time=utc_now_iso(), node_id=service.node_id).model_dump()
            )


async def _heartbeat_loop(conn: ClientConnection, service: ChatService, settings: Settings) -> None:
    """Renova a presença e detecta conexões mortas.

    O envio falho é o sinal de morte: ``send_json`` devolve ``False`` quando o
    socket já não aceita escrita, e aí encerramos a conexão em vez de manter um
    fantasma consumindo um slot e aparecendo na lista de presença.
    """
    while True:
        await asyncio.sleep(settings.heartbeat_interval)
        alive = await conn.send_json(
            PongFrame(server_time=utc_now_iso(), node_id=service.node_id).model_dump()
        )
        if not alive:
            await conn.close(code=1001, reason="heartbeat falhou")
            return
        await service.heartbeat(conn)
