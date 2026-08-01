"""Token bucket por sessão.

Contém uma sessão abusiva sem afetar as demais (FR-13). A escolha de *token
bucket* em vez de janela fixa é deliberada: janela fixa permite o dobro da taxa
na virada da janela (rajada no fim de uma, rajada no início da outra), enquanto
o bucket suaviza naturalmente e ainda admite uma rajada legítima limitada pela
capacidade.

Sem estado compartilhado entre nós: o limite é por sessão, e uma sessão vive em
um único nó. Coordenar limites entre nós exigiria um round-trip ao Redis por
mensagem — caro no caminho quente, para resolver um problema que não existe.
"""

from __future__ import annotations

import time

__all__ = ["TokenBucket"]


class TokenBucket:
    """Bucket que reabastece a ``rate`` tokens por segundo, com teto ``capacity``."""

    __slots__ = ("_capacity", "_rate", "_tokens", "_updated")

    def __init__(self, rate: float, capacity: int | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate deve ser positiva")
        self._rate = rate
        self._capacity = float(capacity if capacity is not None else rate * 2)
        self._tokens = self._capacity
        self._updated = time.monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        """Consome ``cost`` tokens se houver. Devolve ``False`` se estourou o limite."""
        now = time.monotonic()
        elapsed = now - self._updated
        self._updated = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)

        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False

    @property
    def tokens(self) -> float:
        return self._tokens

    def retry_after(self, cost: float = 1.0) -> float:
        """Segundos até que ``cost`` tokens estejam disponíveis."""
        deficit = cost - self._tokens
        return max(0.0, deficit / self._rate)
