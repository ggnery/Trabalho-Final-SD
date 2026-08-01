"""Testes dos relógios lógicos (S-008, S-009 / FR-6).

São os testes que provam, sem nenhuma infraestrutura, que a implementação dos
algoritmos de Lamport e do relógio vetorial está correta.
"""

from __future__ import annotations

import pytest

from salaviva.domain.clocks import CausalOrder, LamportClock, VectorClock

# ---------------------------------------------------------------------------
# Relógio de Lamport
# ---------------------------------------------------------------------------


def test_lamport_comeca_em_zero_e_avanca_de_um():
    clock = LamportClock("A")
    assert clock.value == 0
    assert clock.tick() == 1
    assert clock.tick() == 2
    assert clock.value == 2


def test_lamport_update_aplica_max_mais_um():
    """Regra 2 de Lamport: L := max(L, L_msg) + 1."""
    clock = LamportClock("A", initial=5)
    assert clock.update(10) == 11  # o recebido é maior: salta para 11
    assert clock.update(3) == 12  # o recebido é menor: apenas avança 1


def test_lamport_nunca_regride():
    """Propriedade fundamental: o relógio é monotônico não-decrescente."""
    clock = LamportClock("A")
    anterior = clock.value
    for recebido in [7, 2, 9, 1, 100, 3, 50]:
        atual = clock.update(recebido)
        assert atual > anterior, "o relógio de Lamport regrediu"
        anterior = atual


def test_lamport_garante_happened_before():
    """Se a → b, então L(a) < L(b).

    Cenário: A envia (evento a) → B recebe e envia (evento b) → C recebe.
    A cadeia causal a → b → c precisa aparecer como carimbos crescentes.
    """
    a, b, c = LamportClock("A"), LamportClock("B"), LamportClock("C")

    stamp_a = a.tick()  # evento a, em A
    b.update(stamp_a)  # B recebe a
    stamp_b = b.tick()  # evento b, em B (causado por a)
    c.update(stamp_b)  # C recebe b
    stamp_c = c.tick()  # evento c, em C (causado por b)

    assert stamp_a < stamp_b < stamp_c


def test_lamport_eventos_concorrentes_podem_empatar():
    """A limitação conhecida do relógio escalar.

    Dois nós que nunca se comunicaram produzem carimbos iguais para eventos
    independentes — daí a impossibilidade de inferir causalidade a partir do
    valor, e a razão de o sistema manter também um relógio vetorial (ADR-005).
    """
    a, b = LamportClock("A"), LamportClock("B")
    assert a.tick() == b.tick() == 1  # concorrentes, mesmo carimbo


def test_lamport_rejeita_valores_negativos():
    with pytest.raises(ValueError):
        LamportClock("A", initial=-1)
    with pytest.raises(ValueError):
        LamportClock("A").update(-5)


# ---------------------------------------------------------------------------
# Relógio vetorial
# ---------------------------------------------------------------------------


def test_vector_tick_incrementa_apenas_o_proprio_componente():
    v = VectorClock("A")
    assert v.tick() == {"A": 1}
    assert v.tick() == {"A": 2}


def test_vector_merge_incrementa_proprio_e_toma_max():
    v = VectorClock("A", {"A": 2, "B": 1})
    resultado = v.merge({"A": 1, "B": 5, "C": 3})
    assert resultado == {"A": 3, "B": 5, "C": 3}


def test_vector_merge_incorpora_no_desconhecido():
    """Um nó novo criado pelo Auto Scaling passa a ser conhecido sem configuração."""
    v = VectorClock("A")
    v.tick()
    resultado = v.merge({"Z": 7})
    assert resultado["Z"] == 7


def test_vector_compare_detecta_precedencia():
    antes = {"A": 1, "B": 0}
    depois = {"A": 2, "B": 1}
    assert VectorClock.compare(antes, depois) is CausalOrder.BEFORE
    assert VectorClock.compare(depois, antes) is CausalOrder.AFTER


def test_vector_compare_detecta_igualdade():
    assert VectorClock.compare({"A": 1}, {"A": 1}) is CausalOrder.EQUAL
    # Componente ausente equivale a zero.
    assert VectorClock.compare({"A": 1}, {"A": 1, "B": 0}) is CausalOrder.EQUAL


def test_vector_compare_detecta_concorrencia():
    """O caso que o relógio de Lamport não consegue distinguir.

    A avançou no seu componente, B no dele, sem contato entre eles: nenhum dos
    dois vetores domina o outro.
    """
    va = {"A": 2, "B": 1}
    vb = {"A": 1, "B": 2}
    assert VectorClock.compare(va, vb) is CausalOrder.CONCURRENT
    assert VectorClock.is_concurrent(va, vb)


def test_vector_dois_nos_isolados_produzem_eventos_concorrentes():
    a, b = VectorClock("A"), VectorClock("B")
    va = a.tick()
    vb = b.tick()
    assert VectorClock.is_concurrent(va, vb)


def test_vector_cadeia_causal_nao_e_concorrente():
    """Após a comunicação, os eventos deixam de ser concorrentes."""
    a, b = VectorClock("A"), VectorClock("B")
    va = a.tick()
    vb = b.merge(va)  # B recebeu de A: agora há relação causal
    assert VectorClock.compare(va, vb) is CausalOrder.BEFORE


def test_vector_snapshot_e_copia_defensiva():
    v = VectorClock("A")
    snap = v.snapshot()
    v.tick()
    assert snap == {"A": 0}, "o snapshot não pode acompanhar mutações posteriores"


def test_lamport_e_vetorial_concordam_em_cadeia_causal():
    """Consistência entre os dois mecanismos numa cadeia A → B → C."""
    la, lb, lc = LamportClock("A"), LamportClock("B"), LamportClock("C")
    va, vb, vc = VectorClock("A"), VectorClock("B"), VectorClock("C")

    sa, ca = la.tick(), va.tick()
    lb.update(sa)
    cb = vb.merge(ca)
    sb = lb.tick()
    vb.tick()
    lc.update(sb)
    cc = vc.merge(vb.snapshot())

    assert sa < sb  # Lamport: cadeia crescente
    assert VectorClock.compare(ca, cb) is CausalOrder.BEFORE
    assert VectorClock.compare(cb, cc) is CausalOrder.BEFORE
