"""Configuração do nó, lida de variáveis de ambiente.

A mesma imagem roda em três contextos — Docker Compose local, EC2 e testes — e
a única coisa que muda entre eles são estas variáveis. Nenhum caminho de código
verifica "estou na AWS?": a diferença é sempre injetada, nunca detectada.
"""

from __future__ import annotations

import os
import socket
import uuid

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


def _default_node_id() -> str:
    """Identidade do nó.

    Prioriza o hostname (no Compose é o nome do serviço; na EC2, o ID da
    instância via ``user_data``) porque um identificador reconhecível é o que
    permite, durante a demonstração de falha, correlacionar o que sumiu do
    painel com a instância que foi derrubada. O sufixo aleatório é o fallback
    quando o hostname não é informativo.
    """
    if env_id := os.getenv("NODE_ID"):
        return env_id
    host = socket.gethostname()
    return host if host and host != "localhost" else f"node-{uuid.uuid4().hex[:6]}"


class Settings(BaseSettings):
    """Configuração completa do nó."""

    model_config = SettingsConfigDict(
        env_prefix="SALAVIVA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Identidade -------------------------------------------------------
    node_id: str = Field(default_factory=_default_node_id)
    environment: str = "local"

    # --- Servidor ---------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Redis ------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_max_backoff: float = 30.0
    """Teto do backoff de reconexão. Com jitter, evita que os N nós do cluster
    reconectem em sincronia e criem um *thundering herd* no Redis logo após uma
    partição — o modo de falha em que a recuperação parcial vira queda total."""

    # --- DynamoDB ---------------------------------------------------------
    aws_region: str = "us-east-1"
    dynamo_endpoint_url: str | None = None
    """Aponta para DynamoDB Local no Compose; ``None`` usa o serviço real."""

    messages_table: str = "salaviva_messages"
    rooms_table: str = "salaviva_rooms"
    message_ttl_days: int = 7

    persistence_enabled: bool = True
    """Desligado no Compose sem credenciais AWS. O chat continua funcionando
    em tempo real — só o replay de histórico fica indisponível (ADR-008)."""

    # --- Autenticação -----------------------------------------------------
    # Default apenas para desenvolvimento local. Na AWS o valor vem do SSM
    # Parameter Store (SecureString) via instance profile — nunca da imagem
    # nem do user_data.
    jwt_secret: str = "dev-secret-trocar-em-producao"  # noqa: S105
    jwt_algorithm: str = "HS256"
    jwt_ttl_hours: int = 12

    # --- Limites e temporizações -----------------------------------------
    max_content_length: int = 4096
    rate_limit_per_second: float = 20.0
    rate_limit_burst: int = 40

    heartbeat_interval: int = 20
    """Intervalo do ping do servidor ao cliente, em segundos."""

    presence_ttl: int = 15
    """Idade máxima de um heartbeat antes de o membro ser varrido."""

    sweeper_interval: int = 5
    node_heartbeat_interval: int = 5

    backlog_limit: int = 200
    idempotency_ttl: int = 300

    # --- Observabilidade --------------------------------------------------
    log_level: str = "info"
    log_json: bool = True

    @property
    def dynamo_ttl_seconds(self) -> int:
        return self.message_ttl_days * 86_400


_settings: Settings | None = None


def get_settings() -> Settings:
    """Instância única de :class:`Settings` do processo."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings
