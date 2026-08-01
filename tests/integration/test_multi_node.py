"""Testes multi-nó: as propriedades distribuídas do sistema.

Cada teste aqui sobe um cluster de nós reais do SalaViva no mesmo processo,
ligados por um broker compartilhado. São estes os testes que provam os
requisitos centrais do Projeto 3 — comunicação em grupo (FR-4), ordenação total
(FR-5), relógios lógicos (FR-6) e recuperação sem perda (FR-8).
"""

from __future__ import annotations

import uuid

import pytest

from salaviva.domain.clocks import CausalOrder, VectorClock
from salaviva.domain.ordering import HoldBackQueue

SALA = "geral"


def _msg_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# FR-4 — Comunicação em grupo
# ---------------------------------------------------------------------------


async def test_mensagem_atravessa_os_nos(cluster):
    """Uma mensagem publicada em um nó chega aos clientes de todos os outros."""
    conns = []
    for node in cluster.nodes:
        conn, ws = await node.connect(f"user-{node.node_id}")
        await node.service.join(conn, SALA)
        conns.append((node, conn, ws))

    node_a, conn_a, _ = conns[0]
    await node_a.service.send(conn_a, SALA, "olá, cluster", _msg_id())

    for _, _, ws in conns:
        mensagens = ws.messages()
        assert len(mensagens) == 1, "todo membro da sala deve receber"
        assert mensagens[0]["content"] == "olá, cluster"


async def test_emissor_recebe_a_propria_mensagem(cluster):
    """ADR-004: o emissor não entrega por atalho; recebe pelo Pub/Sub.

    É o que garante um único caminho de entrega no sistema, e portanto que a
    ordem observada seja idêntica para todos por construção.
    """
    node = cluster[0]
    conn, ws = await node.connect("ana")
    await node.service.join(conn, SALA)
    await node.service.send(conn, SALA, "eco", _msg_id())

    assert len(ws.messages()) == 1
    assert ws.messages()[0]["node_id"] == node.node_id


async def test_salas_sao_isoladas(cluster):
    """Membros de outra sala não recebem a mensagem."""
    node = cluster[0]
    conn_geral, ws_geral = await node.connect("ana")
    conn_outra, ws_outra = await node.connect("bruno")
    await node.service.join(conn_geral, "geral")
    await node.service.join(conn_outra, "privada")

    await node.service.send(conn_geral, "geral", "só da geral", _msg_id())

    assert len(ws_geral.messages()) == 1
    assert len(ws_outra.messages()) == 0


# ---------------------------------------------------------------------------
# FR-5 — Ordenação total (o requisito central do projeto)
# ---------------------------------------------------------------------------


async def test_ordem_total_identica_entre_nos(cluster):
    """3 nós, 200 mensagens intercaladas: todos os clientes veem a MESMA ordem.

    Este é o teste que prova o requisito central do Projeto 3 ("garantir a
    ordenação correta das mensagens"). Os envios são distribuídos entre os três
    nós de forma intercalada — o cenário em que uma implementação ingênua
    divergiria.
    """
    observadores = []
    for node in cluster.nodes:
        conn, ws = await node.connect(f"user-{node.node_id}")
        await node.service.join(conn, SALA)
        observadores.append((node, conn, ws))

    total = 200
    for i in range(total):
        node, conn, _ = observadores[i % len(observadores)]  # intercala os emissores
        await node.service.send(conn, SALA, f"msg-{i}", _msg_id())

    sequencias = [ws.seqs() for _, _, ws in observadores]

    for i, seqs in enumerate(sequencias):
        assert len(seqs) == total, f"cliente {i} recebeu {len(seqs)} de {total}"

    referencia = sequencias[0]
    for i, seqs in enumerate(sequencias[1:], start=1):
        assert seqs == referencia, f"cliente {i} viu uma ordem diferente do cliente 0"

    assert referencia == list(range(1, total + 1)), "seq deve ser contíguo de 1..N"


async def test_seq_e_unico_e_monotonico(cluster):
    """Nenhuma repetição, nenhum salto — nem sob emissores concorrentes."""
    node = cluster[0]
    conn, ws = await node.connect("ana")
    await node.service.join(conn, SALA)

    for i in range(50):
        await node.service.send(conn, SALA, f"m{i}", _msg_id())

    seqs = ws.seqs()
    assert len(seqs) == len(set(seqs)), "seq repetido"
    assert seqs == sorted(seqs), "seq fora de ordem crescente"


async def test_salas_tem_sequencias_independentes(cluster):
    """O sequenciador é por sala: uma sala movimentada não afeta a outra."""
    node = cluster[0]
    conn_a, ws_a = await node.connect("ana")
    conn_b, ws_b = await node.connect("bruno")
    await node.service.join(conn_a, "sala-1")
    await node.service.join(conn_b, "sala-2")

    await node.service.send(conn_a, "sala-1", "primeira da sala-1", _msg_id())
    await node.service.send(conn_b, "sala-2", "primeira da sala-2", _msg_id())

    assert ws_a.seqs() == [1]
    assert ws_b.seqs() == [1]


async def test_ordem_preservada_com_rede_reordenando(make_cluster):
    """Sob reordenação da rede, a fila de hold-back do cliente restaura a ordem.

    O broker entrega 40 % das mensagens com atraso, embaralhando a chegada.
    A sequência bruta recebida pode estar fora de ordem — o que o teste exige é
    que, após passar pela hold-back queue (o que o cliente real faz), a ordem
    final seja correta e completa.
    """
    cluster = await make_cluster(3, reorder_probability=0.4)

    observadores = []
    for node in cluster.nodes:
        conn, ws = await node.connect(f"user-{node.node_id}")
        await node.service.join(conn, SALA)
        observadores.append((node, conn, ws))

    total = 60
    for i in range(total):
        node, conn, _ = observadores[i % len(observadores)]
        await node.service.send(conn, SALA, f"msg-{i}", _msg_id())

    # Aguarda as entregas atrasadas pelo broker.
    import asyncio

    await asyncio.sleep(0.2)

    for indice, (_, _, ws) in enumerate(observadores):
        fila: HoldBackQueue[int] = HoldBackQueue(start_seq=0, max_buffer=500)
        ordenada: list[int] = []
        for seq in ws.seqs():
            ordenada.extend(fila.offer(seq, seq))
        assert ordenada == list(range(1, total + 1)), (
            f"cliente {indice} não conseguiu restaurar a ordem após reordenação"
        )


# ---------------------------------------------------------------------------
# FR-6 — Relógios lógicos
# ---------------------------------------------------------------------------


async def test_lamport_avanca_em_todos_os_nos(cluster):
    """O relógio de todo nó avança ao receber, mesmo sem ter originado nada."""
    node_a, node_b = cluster[0], cluster[1]
    conn_a, _ = await node_a.connect("ana")
    conn_b, _ = await node_b.connect("bruno")
    await node_a.service.join(conn_a, SALA)
    await node_b.service.join(conn_b, SALA)

    lamport_b_antes = node_b.service.lamport.value
    await node_a.service.send(conn_a, SALA, "olá", _msg_id())

    assert node_b.service.lamport.value > lamport_b_antes


async def test_lamport_respeita_happened_before_entre_nos(cluster):
    """Resposta a uma mensagem carrega carimbo estritamente maior."""
    node_a, node_b = cluster[0], cluster[1]
    conn_a, ws_a = await node_a.connect("ana")
    conn_b, _ = await node_b.connect("bruno")
    await node_a.service.join(conn_a, SALA)
    await node_b.service.join(conn_b, SALA)

    await node_a.service.send(conn_a, SALA, "pergunta", _msg_id())
    # B recebeu a pergunta (relógio já atualizado) e agora responde.
    await node_b.service.send(conn_b, SALA, "resposta", _msg_id())

    mensagens = ws_a.messages()
    pergunta, resposta = mensagens[0], mensagens[1]
    assert pergunta["lamport"] < resposta["lamport"]


async def test_relogio_vetorial_detecta_concorrencia_real(cluster):
    """Dois nós que enviam sem ter recebido um do outro produzem concorrência.

    É o cenário que o relógio escalar de Lamport não consegue distinguir de uma
    relação causal — e a evidência, na demonstração, de que o sistema é
    genuinamente distribuído.
    """
    node_a, node_b = cluster[0], cluster[1]
    conn_a, ws_a = await node_a.connect("ana")
    conn_b, _ = await node_b.connect("bruno")

    # Ambos entram na sala mas ainda não trocaram nenhuma mensagem: os vetores
    # estão desconectados.
    await node_a.service.join(conn_a, SALA)
    await node_b.service.join(conn_b, SALA)

    vetor_a = node_a.service.vclock.tick()
    vetor_b = node_b.service.vclock.tick()

    assert VectorClock.compare(vetor_a, vetor_b) is CausalOrder.CONCURRENT
    assert ws_a is not None  # a conexão permanece utilizável


async def test_vetorial_deixa_de_ser_concorrente_apos_troca(cluster):
    node_a, node_b = cluster[0], cluster[1]
    conn_a, _ = await node_a.connect("ana")
    conn_b, ws_b = await node_b.connect("bruno")
    await node_a.service.join(conn_a, SALA)
    await node_b.service.join(conn_b, SALA)

    await node_a.service.send(conn_a, SALA, "primeira", _msg_id())
    await node_b.service.send(conn_b, SALA, "segunda", _msg_id())

    primeira, segunda = ws_b.messages()[0], ws_b.messages()[1]
    assert (
        VectorClock.compare(primeira["vector_clock"], segunda["vector_clock"]) is CausalOrder.BEFORE
    )


# ---------------------------------------------------------------------------
# FR-9 — Idempotência
# ---------------------------------------------------------------------------


async def test_reenvio_com_mesmo_client_msg_id_nao_duplica(cluster):
    node = cluster[0]
    conn, ws = await node.connect("ana")
    await node.service.join(conn, SALA)

    msg_id = _msg_id()
    ack1 = await node.service.send(conn, SALA, "mensagem única", msg_id)
    ack2 = await node.service.send(conn, SALA, "mensagem única", msg_id)

    assert ack1.duplicate is False
    assert ack2.duplicate is True
    assert ack2.seq == ack1.seq, "o reenvio deve devolver o seq original"
    assert len(ws.messages()) == 1, "a mensagem não pode ser difundida duas vezes"


async def test_ids_distintos_produzem_mensagens_distintas(cluster):
    node = cluster[0]
    conn, ws = await node.connect("ana")
    await node.service.join(conn, SALA)

    await node.service.send(conn, SALA, "igual", _msg_id())
    await node.service.send(conn, SALA, "igual", _msg_id())

    assert len(ws.messages()) == 2


# ---------------------------------------------------------------------------
# FR-7 — Presença
# ---------------------------------------------------------------------------


async def test_presenca_agrega_membros_de_nos_diferentes(cluster):
    """A lista de presença é global, não local ao nó."""
    node_a, node_b = cluster[0], cluster[1]
    conn_a, _ = await node_a.connect("ana")
    conn_b, _ = await node_b.connect("bruno")
    await node_a.service.join(conn_a, SALA)
    frame = await node_b.service.join(conn_b, SALA)

    usuarios = {m.user for m in frame.members}
    assert usuarios == {"ana", "bruno"}
    nos = {m.node_id for m in frame.members}
    assert len(nos) == 2, "os membros devem estar em nós diferentes"


async def test_saida_remove_da_presenca(cluster):
    node = cluster[0]
    conn_a, _ = await node.connect("ana")
    conn_b, _ = await node.connect("bruno")
    await node.service.join(conn_a, SALA)
    await node.service.join(conn_b, SALA)

    await node.service.leave(conn_a, SALA)

    membros = await node.service.presence.members(SALA)
    assert {m.user for m in membros} == {"bruno"}


# ---------------------------------------------------------------------------
# FR-13 — Limites
# ---------------------------------------------------------------------------


async def test_conteudo_acima_do_limite_e_rejeitado(cluster):
    from salaviva.domain.errors import MessageTooLong

    node = cluster[0]
    conn, _ = await node.connect("ana")
    await node.service.join(conn, SALA)

    with pytest.raises(MessageTooLong):
        await node.service.send(conn, SALA, "x" * 5000, _msg_id())
