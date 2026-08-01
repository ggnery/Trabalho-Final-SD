"""Exceções de domínio.

Cada erro carrega um ``code`` estável, que é o que o cliente recebe no protocolo
WebSocket. Códigos estáveis permitem que o cliente reaja programaticamente
(ex.: aplicar backoff em ``rate_limited``, reautenticar em ``auth_failed``) sem
inspecionar mensagens de texto em português.
"""

from __future__ import annotations

__all__ = [
    "AuthenticationFailed",
    "InvalidProtocolMessage",
    "MessageTooLong",
    "NotInRoom",
    "RateLimitExceeded",
    "RoomNotFound",
    "SalaVivaError",
]


class SalaVivaError(Exception):
    """Raiz de toda exceção de domínio do sistema."""

    code = "internal_error"
    ws_close_code = 1011  # Internal Error (RFC 6455)

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.__doc__ or self.code)
        self.message = message or (self.__doc__ or self.code)

    def to_wire(self) -> dict:
        """Serializa para o frame de erro do protocolo."""
        return {"type": "error", "code": self.code, "message": self.message}


class AuthenticationFailed(SalaVivaError):
    """Token ausente, inválido ou expirado."""

    code = "auth_failed"
    ws_close_code = 4401  # faixa privada: reservada à aplicação


class RoomNotFound(SalaVivaError):
    """A sala solicitada não existe."""

    code = "room_not_found"


class NotInRoom(SalaVivaError):
    """A sessão tentou operar em uma sala da qual não participa."""

    code = "not_in_room"


class MessageTooLong(SalaVivaError):
    """Conteúdo excede o limite de 4096 caracteres."""

    code = "message_too_long"


class RateLimitExceeded(SalaVivaError):
    """Sessão excedeu o limite de mensagens por segundo.

    Não fecha a conexão: conter o abuso não deve punir o usuário que estourou o
    limite por um pico legítimo, e derrubar a conexão custaria mais ao servidor
    (reconexão, replay de backlog) do que simplesmente descartar o excedente.
    """

    code = "rate_limited"


class InvalidProtocolMessage(SalaVivaError):
    """Frame recebido não corresponde a nenhum comando válido do protocolo."""

    code = "invalid_message"
