"""Fila de hold-back: reordenação de mensagens que chegam fora de ordem.

O sequenciador garante que os números de sequência de uma sala são únicos e
monotônicos, mas **não** garante que cheguem em ordem ao cliente: o fan-out do
Pub/Sub, a rede e a reconexão podem entregar 3 antes de 2.

A fila de hold-back é o mecanismo clássico de entrega ordenada (Coulouris,
§15.4): mensagens fora de ordem ficam retidas em um buffer até que a lacuna seja
preenchida; só então a sequência contígua é liberada de uma vez.

Este módulo é puro e é usado nos dois lados — servidor (testes de integração) e
cliente CLI —, o que garante que ambos concordem sobre o que "ordenado"
significa.
"""

from __future__ import annotations

from typing import Generic, TypeVar

__all__ = ["HoldBackQueue"]

T = TypeVar("T")


class HoldBackQueue(Generic[T]):
    """Libera itens apenas em sequência contígua crescente.

    Args:
        start_seq: último ``seq`` já entregue. O próximo esperado é
            ``start_seq + 1``. Ao reconectar, o cliente passa aqui o seu
            ``last_seq``, e a fila retoma exatamente de onde parou.
        max_buffer: teto de itens retidos. Impede que uma lacuna permanente
            (mensagem que nunca chegará, porque o Pub/Sub é *at-most-once*)
            consuma memória sem limite.

    Exemplo:
        >>> q: HoldBackQueue[str] = HoldBackQueue(start_seq=0)
        >>> q.offer(3, "c")          # fora de ordem: retém
        []
        >>> q.offer(1, "a")          # libera 1, mas 2 ainda falta
        ['a']
        >>> q.offer(2, "b")          # preenche a lacuna: libera 2 e 3
        ['b', 'c']
    """

    __slots__ = ("_buffer", "_delivered", "_max_buffer")

    def __init__(self, start_seq: int = 0, max_buffer: int = 1000) -> None:
        if max_buffer < 1:
            raise ValueError("max_buffer deve ser positivo")
        self._delivered = start_seq
        self._buffer: dict[int, T] = {}
        self._max_buffer = max_buffer

    @property
    def last_delivered(self) -> int:
        """Maior ``seq`` já liberado."""
        return self._delivered

    @property
    def expected(self) -> int:
        """Próximo ``seq`` aguardado."""
        return self._delivered + 1

    @property
    def pending_count(self) -> int:
        """Quantos itens estão retidos aguardando uma lacuna."""
        return len(self._buffer)

    @property
    def has_gap(self) -> bool:
        """``True`` quando há itens retidos por causa de uma lacuna."""
        return bool(self._buffer)

    def offer(self, seq: int, item: T) -> list[T]:
        """Oferece um item e devolve os que se tornaram entregáveis, em ordem.

        Descarta duplicatas (``seq`` já entregue ou já em buffer) — o que torna
        a fila idempotente e, portanto, segura diante do replay de reconexão,
        que pode sobrepor mensagens já recebidas em tempo real.
        """
        if seq <= self._delivered or seq in self._buffer:
            return []  # duplicata

        if len(self._buffer) >= self._max_buffer:
            # A lacuna é permanente ou grande demais. Preferimos entregar fora
            # de ordem — sinalizando a perda — a crescer sem limite.
            return self.force_release(extra=(seq, item))

        self._buffer[seq] = item

        released: list[T] = []
        while (nxt := self._delivered + 1) in self._buffer:
            released.append(self._buffer.pop(nxt))
            self._delivered = nxt
        return released

    def force_release(self, extra: tuple[int, T] | None = None) -> list[T]:
        """Libera tudo o que está retido, em ordem de ``seq``, aceitando a lacuna.

        Usado quando a lacuna persiste além do tolerável (buffer cheio ou
        timeout de resync). A ordem relativa é preservada; o que se perde é a
        garantia de contiguidade — e o chamador tem como saber disso comparando
        ``last_delivered`` antes e depois.
        """
        pending = dict(self._buffer)
        if extra is not None:
            pending[extra[0]] = extra[1]
        self._buffer.clear()

        released = [pending[s] for s in sorted(pending)]
        if pending:
            self._delivered = max(self._delivered, *pending)
        return released

    def missing_range(self) -> tuple[int, int] | None:
        """Intervalo ``(primeiro, último)`` de ``seq`` faltantes, se houver.

        O cliente usa isso para pedir um resync direcionado em vez de recarregar
        o histórico inteiro.
        """
        if not self._buffer:
            return None
        return (self.expected, min(self._buffer) - 1)

    def reset(self, seq: int) -> None:
        """Reposiciona a fila em ``seq``, descartando o buffer.

        Chamado após um resync completo, quando o cliente recarregou o backlog
        e o estado anterior deixou de ser relevante.
        """
        self._delivered = seq
        self._buffer.clear()

    def __repr__(self) -> str:  # pragma: no cover - auxiliar de depuração
        return (
            f"HoldBackQueue(delivered={self._delivered}, "
            f"expected={self.expected}, pending={len(self._buffer)})"
        )
