"""Autenticação por JWT.

O token é **stateless**: qualquer nó do Auto Scaling valida a assinatura
localmente, sem consultar nenhum estado compartilhado. Essa é exatamente a
propriedade que faz autenticação funcionar em cluster elástico — uma sessão
guardada em memória do nó, ou mesmo no Redis, tornaria a validação dependente de
um componente adicional no caminho de cada handshake.

Escopo acadêmico: não há senha. ``POST /auth/login`` aceita qualquer nome de
usuário e emite o token. O que é real — e é o que a disciplina avalia no
seminário de Segurança — é a **verificação criptográfica da assinatura** e o
vínculo entre a identidade do token e tudo o que a sessão publica.
"""

from __future__ import annotations

import time

import jwt
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from salaviva.config import Settings
from salaviva.domain.errors import AuthenticationFailed

__all__ = ["create_token", "decode_token", "router"]

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32, pattern=r"^[\w\-. ]+$")


class LoginResponse(BaseModel):
    token: str
    user: str
    expires_in: int


def create_token(username: str, settings: Settings) -> str:
    now = int(time.time())
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + settings.jwt_ttl_hours * 3600,
        "iss": "salaviva",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, settings: Settings) -> str:
    """Valida o token e devolve o nome de usuário (``sub``).

    Levanta :class:`AuthenticationFailed` para qualquer falha — assinatura
    inválida, expiração, emissor errado ou ``sub`` ausente. Não distinguimos os
    casos na resposta ao cliente: informar *por que* um token falhou entrega ao
    atacante um oráculo de validação.
    """
    if not token:
        raise AuthenticationFailed("token ausente")
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer="salaviva",
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationFailed("token inválido ou expirado") from exc

    username = payload.get("sub")
    if not username:
        raise AuthenticationFailed("token sem identidade")
    return str(username)


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    # As configurações vêm de `app.state`, e não do singleton global: é o
    # objeto passado a `create_app()` que precisa valer em todo lugar. Se o
    # emissor do token lesse um Settings e o validador do WebSocket lesse
    # outro, os dois usariam segredos diferentes e todo token seria recusado.
    settings: Settings = request.app.state.settings
    return LoginResponse(
        token=create_token(body.username, settings),
        user=body.username,
        expires_in=settings.jwt_ttl_hours * 3600,
    )
