"""Configuração de log estruturado.

Todo evento sai como uma linha JSON com ``node_id``. Esse campo é o que
transforma a demonstração de tolerância a falhas em evidência consultável: no
CloudWatch Logs Insights, uma query ``stats count(*) by node_id`` mostra o
tráfego migrando da instância derrubada para as sobreviventes, e o ``seq`` sem
lacuna comprova que nada se perdeu no caminho.

Ler isso em texto corrido, ao vivo, durante 15 minutos de apresentação, não
seria viável.
"""

from __future__ import annotations

import logging
import sys

import structlog

__all__ = ["configure_logging"]


def configure_logging(level: str = "info", json_output: bool = True, node_id: str = "") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)
    # Uvicorn duplica o log de acesso a cada requisição; com health check a cada
    # 15 s vindo do ALB, isso inunda o CloudWatch com ruído sem informação.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    if node_id:
        structlog.contextvars.bind_contextvars(node_id=node_id)
