"""Endpoints de saúde, métricas e observação do cluster.

A separação entre ``/healthz`` e ``/readyz`` é o que torna o failover automático
em vez de manual, e é uma das perguntas prováveis na arguição:

* ``/healthz`` responde se **o processo** está vivo. Um nó que perdeu a conexão
  com o Redis continua passando aqui — ele está de pé, só não serve para nada.
* ``/readyz`` verifica as **dependências**. É este que o ALB consulta. Um nó
  degradado reprova e se autoexclui do pool, em vez de virar um buraco negro
  recebendo tráfego que não consegue atender.

Se o ALB apontasse para ``/healthz``, um nó com Redis inacessível continuaria
recebendo conexões e falhando silenciosamente em cada uma.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

__all__ = ["router"]

router = APIRouter(tags=["ops"])


def _service(request: Request):
    return request.app.state.service


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    """Liveness: o processo está de pé e o event loop responde."""
    return {"status": "alive", "node_id": _service(request).node_id}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict:
    """Readiness: as dependências respondem. Alvo do health check do ALB."""
    service = _service(request)
    bus_ok = await service.bus.healthy()
    repo_ok = await service.repository.healthy()
    ready = bus_ok and repo_ok

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if ready else "degraded",
        "node_id": service.node_id,
        "checks": {"message_bus": bus_ok, "repository": repo_ok},
    }


@router.get("/metrics")
async def metrics(request: Request) -> dict:
    """Métricas deste nó, em JSON.

    Deliberadamente não é formato Prometheus: sem um Prometheus provisionado,
    JSON legível é o que a apresentação consegue exibir — e é o que o painel de
    nós consome.
    """
    return _service(request).metrics()


@router.get("/api/nodes")
async def nodes(request: Request) -> dict:
    """Nós vivos do cluster, segundo o registro no Redis.

    É a fonte do painel usado na demonstração de falha: a instância derrubada
    desaparece daqui em até um ciclo de sweeper, e o substituto criado pelo Auto
    Scaling aparece com um ``node_id`` novo.
    """
    service = _service(request)
    alive = await service.alive_nodes()
    return {
        "self": service.node_id,
        "count": len(alive),
        "nodes": [n.model_dump() for n in alive],
    }
