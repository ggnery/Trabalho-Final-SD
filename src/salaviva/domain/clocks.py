"""Relógios lógicos.

Implementa os dois mecanismos de ordenação de eventos exigidos pelo Projeto 3:

* :class:`LamportClock` — relógio escalar de Lamport (1978), que estabelece a
  relação *happened-before* (→).
* :class:`VectorClock` — relógio vetorial (Fidge/Mattern, 1988), que fecha a
  lacuna do relógio escalar ao tornar a **concorrência** detectável.

Este módulo é deliberadamente puro: nenhum import de rede, disco ou biblioteca
de I/O. Ele é testável em microssegundos sem Redis, sem AWS e sem Docker — ver
``memory-bank/standards/coding-standards.md``.

Nota sobre concorrência
-----------------------
Os métodos mutadores não usam lock. Isso é correto — e não um descuido — porque
todo o servidor roda em um único event loop ``asyncio`` e nenhum desses métodos
contém ``await``. Um bloco sem ponto de suspensão executa atomicamente em
relação às demais corrotinas do loop. Introduzir um ``asyncio.Lock`` aqui
adicionaria pontos de suspensão a uma seção que hoje é atômica, criando
justamente o entrelaçamento que o lock pretenderia evitar.

Referências
-----------
* LAMPORT, L. *Time, Clocks, and the Ordering of Events in a Distributed
  System.* Communications of the ACM, v. 21, n. 7, p. 558-565, 1978.
* MATTERN, F. *Virtual Time and Global States of Distributed Systems.* 1988.
* COULOURIS, G. et al. *Distributed Systems: Concepts and Design.* 5. ed.,
  cap. 14 (Time and Global States).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

__all__ = ["CausalOrder", "LamportClock", "VectorClock"]


class CausalOrder(StrEnum):
    """Resultado da comparação causal entre dois relógios vetoriais."""

    BEFORE = "before"
    """O primeiro evento aconteceu antes do segundo (a → b)."""

    AFTER = "after"
    """O primeiro evento aconteceu depois do segundo (b → a)."""

    EQUAL = "equal"
    """Os relógios são idênticos — mesmo estado causal."""

    CONCURRENT = "concurrent"
    """Nenhum dos dois causou o outro. São eventos concorrentes (a ∥ b).

    Este é o caso que o relógio escalar de Lamport **não** consegue distinguir
    de uma relação causal genuína, e é a razão de o sistema manter também um
    relógio vetorial (ver ADR-005).
    """


class LamportClock:
    """Relógio lógico escalar de Lamport.

    Mantém um contador inteiro por processo (aqui, por nó do cluster) obedecendo
    às duas regras do artigo original:

    1. Antes de um evento local ou do envio de uma mensagem: ``L := L + 1``.
    2. Ao receber uma mensagem carimbada com ``L_msg``:
       ``L := max(L, L_msg) + 1``.

    A propriedade garantida é: se o evento ``a`` causou o evento ``b``
    (``a → b``), então ``L(a) < L(b)``.

    A recíproca **não** vale: ``L(a) < L(b)`` não implica ``a → b``, pois os
    eventos podem ser concorrentes. É por isso que este relógio não é usado
    para definir a ordem de entrega das mensagens — esse papel cabe ao
    sequenciador de ordem total (ADR-003).

    Exemplo:
        >>> a = LamportClock("node-a")
        >>> b = LamportClock("node-b")
        >>> stamp = a.tick()          # nó A envia
        >>> stamp
        1
        >>> b.update(stamp)           # nó B recebe
        2
        >>> b.value > stamp           # a → b implica L(a) < L(b)
        True
    """

    __slots__ = ("_value", "node_id")

    def __init__(self, node_id: str, initial: int = 0) -> None:
        if initial < 0:
            raise ValueError("O relógio de Lamport não admite valor inicial negativo")
        self.node_id = node_id
        self._value = initial

    @property
    def value(self) -> int:
        """Valor atual do relógio, sem alterá-lo."""
        return self._value

    def tick(self) -> int:
        """Registra um evento local (ou um envio) e devolve o novo valor.

        Regra 1 de Lamport: ``L := L + 1``.
        """
        self._value += 1
        return self._value

    def update(self, received: int) -> int:
        """Registra o recebimento de uma mensagem carimbada com ``received``.

        Regra 2 de Lamport: ``L := max(L, received) + 1``.

        O ``max`` é o que faz o relógio nunca regredir mesmo quando chega uma
        mensagem de um nó "atrasado", e o ``+1`` é o que garante que o evento
        de recebimento seja estritamente posterior ao evento de envio.
        """
        if received < 0:
            raise ValueError("Carimbo de Lamport recebido não pode ser negativo")
        self._value = max(self._value, received) + 1
        return self._value

    def happened_before(self, other_stamp: int) -> bool:
        """Indica se o estado atual precede ``other_stamp`` na ordem de Lamport."""
        return self._value < other_stamp

    def __repr__(self) -> str:  # pragma: no cover - auxiliar de depuração
        return f"LamportClock(node_id={self.node_id!r}, value={self._value})"


class VectorClock:
    """Relógio vetorial: um contador por nó conhecido.

    Ao contrário do relógio escalar, o vetorial caracteriza a causalidade com
    exatidão: dados dois carimbos, é sempre possível decidir se um precede o
    outro **ou se são concorrentes**.

    Regras:

    1. Evento local ou envio: ``V[self] := V[self] + 1``.
    2. Recebimento de ``V_msg``: primeiro ``V[self] += 1``, depois
       ``V[k] := max(V[k], V_msg[k])`` para todo ``k``.

    Comparação (``compare``): ``V_a < V_b`` se ``V_a[k] <= V_b[k]`` para todo
    ``k`` e existe ao menos um ``k`` com ``V_a[k] < V_b[k]``. Se nem
    ``V_a < V_b`` nem ``V_b < V_a``, os eventos são **concorrentes**.

    O sistema usa esta classe para diagnóstico, não para entrega: o cliente CLI
    marca visualmente as mensagens concorrentes, o que torna observável, durante
    a apresentação, o fato de o sistema ser genuinamente distribuído —
    concorrência só surge quando há mais de um nó originando eventos.

    Exemplo:
        >>> a, b = VectorClock("A"), VectorClock("B")
        >>> va = a.tick()                       # evento em A
        >>> vb = b.tick()                       # evento em B, sem contato com A
        >>> VectorClock.compare(va, vb) is CausalOrder.CONCURRENT
        True
    """

    __slots__ = ("_vec", "node_id")

    def __init__(self, node_id: str, initial: Mapping[str, int] | None = None) -> None:
        self.node_id = node_id
        self._vec: dict[str, int] = dict(initial) if initial else {}
        self._vec.setdefault(node_id, 0)

    def snapshot(self) -> dict[str, int]:
        """Cópia defensiva do vetor, segura para serializar no envelope."""
        return dict(self._vec)

    @property
    def value(self) -> int:
        """Componente do próprio nó."""
        return self._vec[self.node_id]

    def tick(self) -> dict[str, int]:
        """Registra um evento local (ou envio) e devolve o novo vetor."""
        self._vec[self.node_id] = self._vec.get(self.node_id, 0) + 1
        return self.snapshot()

    def merge(self, other: Mapping[str, int]) -> dict[str, int]:
        """Registra o recebimento de uma mensagem carimbada com ``other``.

        Incrementa o próprio componente e depois toma o máximo componente a
        componente. Nós desconhecidos presentes em ``other`` são incorporados —
        é assim que um nó recém-adicionado pelo Auto Scaling passa a ser
        conhecido pelos demais sem nenhuma configuração.
        """
        self._vec[self.node_id] = self._vec.get(self.node_id, 0) + 1
        for node, counter in other.items():
            if counter > self._vec.get(node, 0):
                self._vec[node] = counter
        return self.snapshot()

    @staticmethod
    def compare(a: Mapping[str, int], b: Mapping[str, int]) -> CausalOrder:
        """Classifica a relação causal entre dois carimbos vetoriais.

        Retorna :class:`CausalOrder`. Componentes ausentes valem zero, o que
        permite comparar carimbos gerados quando conjuntos diferentes de nós
        estavam ativos.
        """
        keys = a.keys() | b.keys()
        a_le_b = True  # a <= b em todos os componentes
        b_le_a = True  # b <= a em todos os componentes
        strict = False  # existe algum componente estritamente diferente

        for k in keys:
            av, bv = a.get(k, 0), b.get(k, 0)
            if av > bv:
                a_le_b = False
            elif av < bv:
                b_le_a = False
            if av != bv:
                strict = True

        if not strict:
            return CausalOrder.EQUAL
        if a_le_b:
            return CausalOrder.BEFORE
        if b_le_a:
            return CausalOrder.AFTER
        return CausalOrder.CONCURRENT

    @staticmethod
    def is_concurrent(a: Mapping[str, int], b: Mapping[str, int]) -> bool:
        """Atalho: ``True`` quando nenhum dos dois eventos causou o outro."""
        return VectorClock.compare(a, b) is CausalOrder.CONCURRENT

    def __repr__(self) -> str:  # pragma: no cover - auxiliar de depuração
        return f"VectorClock(node_id={self.node_id!r}, vec={self._vec})"
