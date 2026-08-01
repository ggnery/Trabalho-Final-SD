"""Tolerância a falhas: derrubar um nó não perde mensagem (FR-8 / critério EC3).

Estes testes reproduzem, em memória, exatamente o evento que
``scripts/kill_node.sh`` provoca na AWS: uma instância morre abruptamente,
sem encerramento gracioso, levando junto as conexões WebSocket que atendia.

A propriedade que se prova é a que será demonstrada ao vivo: os clientes daquele
nó reconectam em outro, informam o último ``seq`` que viram, e recebem
**exatamente** o que perderam — sem lacuna e sem duplicata.

O motivo estrutural de isso funcionar é que os dois estados que importam vivem
fora do nó: o número de sequência está no Redis e o histórico está no DynamoDB.
O que morre com a instância é apenas o mapa de conexões e o relógio local — e
o relógio reconverge em uma única mensagem, pela regra do ``max()``.
"""

from __future__ import annotations

import uuid

SALA = "geral"


def _msg_id() -> str:
    return uuid.uuid4().hex


async def test_queda_de_no_nao_perde_mensagem(cluster):
    """Cenário completo da demonstração de falha.

    1. Três nós, um cliente em cada, todos na mesma sala.
    2. Tráfego normal.
    3. O nó B morre abruptamente.
    4. O tráfego continua entre os nós sobreviventes.
    5. O cliente órfão reconecta no nó C informando o ``last_seq``.
    6. Verificação: ele recupera exatamente a lacuna, a sequência final é
       contígua e idêntica à dos clientes que nunca caíram.
    """
    node_a, node_b, node_c = cluster[0], cluster[1], cluster[2]

    conn_a, ws_a = await node_a.connect("ana")
    conn_b, ws_b = await node_b.connect("bruno")
    conn_c, ws_c = await node_c.connect("carla")
    for node, conn in ((node_a, conn_a), (node_b, conn_b), (node_c, conn_c)):
        await node.service.join(conn, SALA)

    # 2. Tráfego normal — todos recebem.
    for i in range(5):
        await node_a.service.send(conn_a, SALA, f"antes-{i}", _msg_id())

    assert ws_b.seqs() == [1, 2, 3, 4, 5]
    ultimo_seq_visto_por_bruno = ws_b.seqs()[-1]

    # 3. O nó B morre. Sem encerramento gracioso, como um terminate-instances.
    node_b.kill()

    # 4. O tráfego continua entre os sobreviventes.
    for i in range(5):
        await node_a.service.send(conn_a, SALA, f"durante-{i}", _msg_id())

    await node_a.drain()  # garante que a persistência assíncrona concluiu

    assert ws_a.seqs() == list(range(1, 11)), "o nó sobrevivente segue recebendo tudo"
    assert ws_c.seqs() == list(range(1, 11))
    assert ws_b.seqs() == [1, 2, 3, 4, 5], "o cliente órfão parou no momento da queda"

    # 5. Bruno reconecta — o ALB o direciona ao nó C.
    conn_bruno_novo, _ws_bruno_novo = await node_c.connect("bruno")
    frame = await node_c.service.join(conn_bruno_novo, SALA, last_seq=ultimo_seq_visto_por_bruno)

    # 6. Recuperou exatamente a lacuna.
    recuperados = [m.seq for m in frame.backlog]
    assert recuperados == [6, 7, 8, 9, 10], "o backlog deve conter exatamente o que faltou"

    sequencia_final_bruno = ws_b.seqs() + recuperados
    assert sequencia_final_bruno == list(range(1, 11))
    assert sequencia_final_bruno == ws_a.seqs(), (
        "após a recuperação, o cliente que caiu vê a mesma sequência de quem nunca caiu"
    )


async def test_sequenciador_sobrevive_a_queda_do_no(cluster):
    """O ``seq`` nunca regride: ele vive no Redis, não no nó.

    Se o contador fosse mantido em memória do nó, a morte de uma instância
    reiniciaria a numeração e duas mensagens diferentes acabariam com o mesmo
    ``seq`` — quebrando a ordem total de forma silenciosa e irrecuperável.
    """
    node_a, node_b = cluster[0], cluster[1]
    conn_a, ws_a = await node_a.connect("ana")
    conn_b, _ = await node_b.connect("bruno")
    await node_a.service.join(conn_a, SALA)
    await node_b.service.join(conn_b, SALA)

    await node_a.service.send(conn_a, SALA, "um", _msg_id())
    await node_b.service.send(conn_b, SALA, "dois", _msg_id())
    seq_antes_da_queda = ws_a.seqs()[-1]

    node_b.kill()

    await node_a.service.send(conn_a, SALA, "tres", _msg_id())
    assert ws_a.seqs()[-1] == seq_antes_da_queda + 1, "a numeração continuou de onde parou"


async def test_no_morto_para_de_receber(cluster):
    """A instância derrubada é desligada do barramento e não recebe mais nada."""
    node_a, node_b = cluster[0], cluster[1]
    conn_a, _ = await node_a.connect("ana")
    conn_b, ws_b = await node_b.connect("bruno")
    await node_a.service.join(conn_a, SALA)
    await node_b.service.join(conn_b, SALA)

    node_b.kill()
    await node_a.service.send(conn_a, SALA, "pós-morte", _msg_id())

    assert ws_b.messages() == [], "um nó morto não pode continuar entregando"


async def test_no_novo_entra_sem_reconfigurar_os_existentes(cluster, settings, broker):
    """Escala horizontal: adicionar um nó não exige tocar em nenhum outro.

    É a consequência direta da comunicação indireta (ADR-002). O nó novo só
    precisa conhecer o Redis; nenhum dos nós existentes é notificado, e ainda
    assim a difusão passa a alcançá-lo.
    """
    from tests.conftest import Node

    node_a = cluster[0]
    conn_a, _ = await node_a.connect("ana")
    await node_a.service.join(conn_a, SALA)
    await node_a.service.send(conn_a, SALA, "antes do novo nó", _msg_id())
    await node_a.drain()

    # Instância criada pelo Auto Scaling, entrando no cluster do zero.
    node_novo = Node("node-novo", broker, settings, cluster.repository)
    await node_novo.start()
    conn_novo, ws_novo = await node_novo.connect("diego")
    frame = await node_novo.service.join(conn_novo, SALA)

    # Recupera o histórico anterior à sua existência.
    assert [m.seq for m in frame.backlog] == [1]

    # E passa a receber o tráfego em tempo real, sem que node_a saiba dele.
    await node_a.service.send(conn_a, SALA, "depois do novo nó", _msg_id())
    assert ws_novo.seqs() == [2]

    await node_novo.bus.stop()


async def test_relogio_de_lamport_reconverge_apos_reinicio(cluster, settings, broker):
    """Um nó reiniciado começa com relógio zerado e reconverge em uma mensagem.

    A regra ``L := max(L, L_msg) + 1`` faz a recuperação ser automática: não há
    procedimento de sincronização de relógio no reingresso ao cluster.
    """
    from tests.conftest import Node

    node_a = cluster[0]
    conn_a, _ = await node_a.connect("ana")
    await node_a.service.join(conn_a, SALA)

    for i in range(10):
        await node_a.service.send(conn_a, SALA, f"m{i}", _msg_id())

    lamport_avancado = node_a.service.lamport.value
    assert lamport_avancado >= 10

    # Instância substituta, com relógio em zero.
    substituto = Node("node-substituto", broker, settings, cluster.repository)
    await substituto.start()
    assert substituto.service.lamport.value == 0

    conn_sub, _ = await substituto.connect("elena")
    await substituto.service.join(conn_sub, SALA)
    await node_a.service.send(conn_a, SALA, "mensagem de reconvergência", _msg_id())

    assert substituto.service.lamport.value > lamport_avancado, (
        "o relógio do nó substituto deve saltar para além do maior valor visto"
    )

    await substituto.bus.stop()


async def test_perda_no_pubsub_e_recuperada_por_resync(make_cluster):
    """Redis Pub/Sub é *at-most-once*: a durabilidade vem do histórico.

    Com 30 % de perda no barramento, um cliente pode não receber tudo em tempo
    real — mas o ``resync``, que lê do repositório durável, recompõe a sala
    integralmente. É o trade-off do ADR-002 sendo exercitado.
    """
    cluster = await make_cluster(2, drop_probability=0.3)
    node_a, node_b = cluster[0], cluster[1]

    conn_a, _ = await node_a.connect("ana")
    conn_b, ws_b = await node_b.connect("bruno")
    await node_a.service.join(conn_a, SALA)
    await node_b.service.join(conn_b, SALA)

    total = 40
    for i in range(total):
        await node_a.service.send(conn_a, SALA, f"m{i}", _msg_id())
    await node_a.drain()

    recebidos_em_tempo_real = set(ws_b.seqs())

    # O cliente detecta a lacuna e pede resync a partir do zero.
    recuperados = await node_b.service.resync(conn_b, SALA, after_seq=0)
    seqs_recuperados = {m.seq for m in recuperados}

    assert seqs_recuperados == set(range(1, total + 1)), (
        "o histórico durável precisa conter tudo, mesmo o que o Pub/Sub perdeu"
    )
    assert recebidos_em_tempo_real.issubset(seqs_recuperados)
