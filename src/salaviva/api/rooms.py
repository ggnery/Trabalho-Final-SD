"""Consulta de salas e histórico via HTTP.

Complementa o WebSocket para os casos em que não faz sentido abrir uma conexão
persistente: a tela de seleção de salas e a inspeção de histórico durante a
apresentação (útil para mostrar, no navegador, que os ``seq`` de uma sala
permanecem contíguos mesmo depois de um nó ter sido derrubado).
"""

from __future__ import annotations

from itertools import pairwise

from fastapi import APIRouter, Query, Request

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["rooms"])


@router.get("/rooms")
async def list_rooms(request: Request) -> dict:
    """Salas com presença ativa, ordenadas por número de membros."""
    service = request.app.state.service
    summaries = await service.rooms()
    return {"count": len(summaries), "rooms": [s.model_dump() for s in summaries]}


@router.get("/rooms/{room_id}/messages")
async def room_messages(
    request: Request,
    room_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """Histórico da sala a partir de ``after_seq``, em ordem crescente.

    Devolve também ``contiguous``: se os ``seq`` retornados formam uma sequência
    sem lacunas. É a verificação que se projeta na tela após a simulação de
    falha para evidenciar que nenhuma mensagem se perdeu.
    """
    service = request.app.state.service
    backlog = await service.repository.backlog(room_id, after_seq=after_seq, limit=limit)
    seqs = [m.seq for m in backlog]
    contiguous = all(b - a == 1 for a, b in pairwise(seqs))
    return {
        "room_id": room_id,
        "count": len(backlog),
        "contiguous": contiguous,
        "first_seq": seqs[0] if seqs else None,
        "last_seq": seqs[-1] if seqs else None,
        "messages": [m.model_dump() for m in backlog],
    }
