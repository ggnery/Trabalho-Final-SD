"""Testes da fila de hold-back (S-007 / FR-5).

A fila é o que garante que uma chegada fora de ordem — inevitável em um sistema
com fan-out por Pub/Sub — nunca vire uma conversa embaralhada na tela.
"""

from __future__ import annotations

import random

import pytest

from salaviva.domain.ordering import HoldBackQueue


def test_entrega_em_ordem_passa_direto():
    q: HoldBackQueue[str] = HoldBackQueue(start_seq=0)
    assert q.offer(1, "a") == ["a"]
    assert q.offer(2, "b") == ["b"]
    assert q.offer(3, "c") == ["c"]
    assert not q.has_gap


def test_chegada_fora_de_ordem_e_reordenada():
    """Chegada 3, 1, 2 é entregue como 1, 2, 3."""
    q: HoldBackQueue[str] = HoldBackQueue(start_seq=0)
    assert q.offer(3, "c") == []  # retém: falta 1 e 2
    assert q.offer(1, "a") == ["a"]  # libera 1; 2 ainda falta
    assert q.offer(2, "b") == ["b", "c"]  # preenche a lacuna: libera 2 e 3
    assert not q.has_gap


def test_duplicata_e_descartada():
    """Idempotência: o replay de reconexão pode sobrepor o que já chegou."""
    q: HoldBackQueue[str] = HoldBackQueue(start_seq=0)
    assert q.offer(1, "a") == ["a"]
    assert q.offer(1, "a-de-novo") == []
    assert q.offer(1, "a-mais-uma-vez") == []


def test_duplicata_em_buffer_e_descartada():
    q: HoldBackQueue[str] = HoldBackQueue(start_seq=0)
    q.offer(5, "e")
    assert q.offer(5, "e-duplicado") == []
    assert q.pending_count == 1


def test_start_seq_permite_retomar_apos_reconexao():
    """O cliente reconecta informando o último seq visto e retoma dali."""
    q: HoldBackQueue[str] = HoldBackQueue(start_seq=100)
    assert q.offer(99, "antiga") == []  # já vista antes da queda
    assert q.offer(101, "nova") == ["nova"]


def test_missing_range_aponta_a_lacuna():
    q: HoldBackQueue[str] = HoldBackQueue(start_seq=0)
    q.offer(5, "e")
    assert q.missing_range() == (1, 4)
    q.offer(1, "a")
    assert q.missing_range() == (2, 4)


def test_missing_range_e_none_sem_lacuna():
    q: HoldBackQueue[str] = HoldBackQueue(start_seq=0)
    assert q.missing_range() is None
    q.offer(1, "a")
    assert q.missing_range() is None


def test_force_release_entrega_tudo_em_ordem():
    """Lacuna permanente: entregamos fora de contiguidade, mas em ordem."""
    q: HoldBackQueue[str] = HoldBackQueue(start_seq=0)
    q.offer(3, "c")
    q.offer(5, "e")
    q.offer(4, "d")
    assert q.force_release() == ["c", "d", "e"]
    assert q.last_delivered == 5
    assert not q.has_gap


def test_buffer_cheio_forca_liberacao():
    """O buffer não pode crescer sem limite diante de uma lacuna permanente."""
    q: HoldBackQueue[str] = HoldBackQueue(start_seq=0, max_buffer=3)
    q.offer(10, "j")
    q.offer(11, "k")
    q.offer(12, "l")
    liberados = q.offer(13, "m")  # estoura o teto
    assert liberados == ["j", "k", "l", "m"]
    assert q.pending_count == 0


def test_reset_reposiciona_e_limpa():
    q: HoldBackQueue[str] = HoldBackQueue(start_seq=0)
    q.offer(5, "e")
    q.reset(10)
    assert q.last_delivered == 10
    assert q.pending_count == 0
    assert q.offer(11, "k") == ["k"]


def test_max_buffer_invalido():
    with pytest.raises(ValueError):
        HoldBackQueue(max_buffer=0)


@pytest.mark.parametrize("semente", [1, 7, 42, 99, 2026])
def test_ordem_final_e_correta_para_qualquer_embaralhamento(semente: int):
    """Propriedade: qualquer permutação de chegada produz a mesma saída ordenada.

    Este é o teste que mais importa — ele verifica a propriedade em vez de um
    caso particular. Independentemente da ordem em que a rede entregue, a fila
    devolve 1..50 exatamente uma vez cada, em ordem crescente.
    """
    rng = random.Random(semente)
    entrada = list(range(1, 51))
    embaralhada = entrada.copy()
    rng.shuffle(embaralhada)

    q: HoldBackQueue[int] = HoldBackQueue(start_seq=0, max_buffer=100)
    saida: list[int] = []
    for seq in embaralhada:
        saida.extend(q.offer(seq, seq))

    assert saida == entrada
    assert not q.has_gap
