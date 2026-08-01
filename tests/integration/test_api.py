"""Testes da borda HTTP e WebSocket com a aplicação real.

Diferentemente de ``test_multi_node.py``, que exercita o serviço diretamente,
aqui a aplicação FastAPI é montada de verdade e acessada pelo protocolo — o
mesmo caminho que o cliente web percorre. É o que cobre autenticação,
validação de protocolo, health checks e o laço de conexão.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from salaviva.config import Settings
from salaviva.main import create_app


@pytest.fixture
def app_settings() -> Settings:
    return Settings(
        redis_url="memory://",
        persistence_enabled=True,
        node_id="node-teste",
        log_json=False,
        jwt_secret="segredo-de-teste-com-32-bytes-no-minimo",
        # Limite alto por padrão: só o teste de rate limit quer o limite baixo,
        # e deixá-lo baixo aqui faria os demais testes esbarrarem nele por
        # acidente — passando a verificar o limitador em vez do que pretendem.
        rate_limit_per_second=500,
        rate_limit_burst=1000,
        heartbeat_interval=3600,  # desliga o ping durante os testes
    )


@pytest.fixture
def client(app_settings: Settings):
    with TestClient(create_app(app_settings)) as c:
        yield c


@pytest.fixture
def client_com_limite_baixo(app_settings: Settings):
    """Cliente com rate limit de 5 msg/s, para exercitar o limitador."""
    apertado = app_settings.model_copy(update={"rate_limit_per_second": 5, "rate_limit_burst": 5})
    with TestClient(create_app(apertado)) as c:
        yield c


def token_de(client: TestClient, user: str) -> str:
    resp = client.post("/auth/login", json={"username": user})
    assert resp.status_code == 200
    return resp.json()["token"]


def receber(ws, tipo: str, limite: int = 60) -> dict:
    """Lê frames até encontrar um do tipo pedido.

    O cliente não pode assumir ordem entre frames de tipos diferentes. Com o
    barramento Redis real, o ``ack`` chega antes do eco da mensagem, porque a
    publicação retorna sem esperar o fan-out. Com o barramento em memória do
    modo standalone, a entrega é síncrona e o eco chega primeiro. Ambas as
    ordens são válidas — o que o protocolo garante é a ordem entre mensagens
    (via ``seq``), não entre categorias de frame.
    """
    for _ in range(limite):
        frame = ws.receive_json()
        if frame["type"] == tipo:
            return frame
    raise AssertionError(f"frame do tipo {tipo!r} não chegou em {limite} frames")


# ---------------------------------------------------------------------------
# Autenticação (FR-1)
# ---------------------------------------------------------------------------


def test_login_emite_token(client: TestClient):
    resp = client.post("/auth/login", json={"username": "ana"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == "ana"
    assert body["expires_in"] > 0
    assert body["token"].count(".") == 2  # header.payload.signature


def test_login_rejeita_usuario_invalido(client: TestClient):
    assert client.post("/auth/login", json={"username": ""}).status_code == 422
    assert client.post("/auth/login", json={"username": "a" * 100}).status_code == 422
    assert client.post("/auth/login", json={"username": "in/valido"}).status_code == 422


def test_websocket_sem_token_e_recusado(client: TestClient):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect("/ws"):
        pass
    assert exc.value.code == 4401


def test_websocket_com_token_adulterado_e_recusado(client: TestClient):
    from starlette.websockets import WebSocketDisconnect

    bom = token_de(client, "ana")
    adulterado = bom[:-4] + "AAAA"  # assinatura inválida
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(f"/ws?token={adulterado}"),
    ):
        pass
    assert exc.value.code == 4401


def test_token_de_outro_segredo_e_recusado(client: TestClient, app_settings: Settings):
    """Um nó não aceita token emitido com outra chave.

    É a propriedade que permite validação stateless: a confiança vem da
    assinatura, não de estado compartilhado entre instâncias.
    """
    from starlette.websockets import WebSocketDisconnect

    from salaviva.api.auth import create_token

    intruso = create_token("mallory", app_settings.model_copy(update={"jwt_secret": "outra"}))
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(f"/ws?token={intruso}"),
    ):
        pass
    assert exc.value.code == 4401


# ---------------------------------------------------------------------------
# Saúde e observabilidade (FR-10)
# ---------------------------------------------------------------------------


def test_healthz_e_liveness(client: TestClient):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive", "node_id": "node-teste"}


def test_readyz_verifica_dependencias(client: TestClient):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"message_bus": True, "repository": True}


def test_readyz_reprova_com_barramento_degradado(client: TestClient):
    """Nó degradado se autoexclui do pool do ALB em vez de virar buraco negro."""
    servico = client.app.state.service
    servico.bus.kill()

    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
    assert resp.json()["checks"]["message_bus"] is False

    # /healthz continua aprovando: o processo está vivo, só não serve tráfego.
    assert client.get("/healthz").status_code == 200


def test_metrics_expoe_estado_do_no(client: TestClient):
    body = client.get("/metrics").json()
    assert body["node_id"] == "node-teste"
    assert "connections" in body
    assert "lamport" in body
    assert "vector_clock" in body


def test_paginas_estaticas_sao_servidas(client: TestClient):
    for caminho in ("/", "/dashboard", "/static/app.js", "/static/style.css"):
        assert client.get(caminho).status_code == 200, caminho


# ---------------------------------------------------------------------------
# Fluxo WebSocket completo
# ---------------------------------------------------------------------------


def test_fluxo_completo_de_chat(client: TestClient):
    token = token_de(client, "ana")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        boas_vindas = ws.receive_json()
        assert boas_vindas["type"] == "welcome"
        assert boas_vindas["node_id"] == "node-teste"
        assert boas_vindas["user"] == "ana"

        ws.send_json({"type": "join", "room": "geral", "last_seq": 0})
        entrou = ws.receive_json()
        assert entrou["type"] == "joined"
        assert entrou["room"] == "geral"
        assert [m["user"] for m in entrou["members"]] == ["ana"]

        ws.send_json(
            {
                "type": "send",
                "room": "geral",
                "content": "primeira mensagem",
                "client_msg_id": "m1",
            }
        )
        eco = receber(ws, "message")
        assert eco["content"] == "primeira mensagem"
        assert eco["seq"] == 1
        assert eco["node_id"] == "node-teste"
        assert eco["lamport"] >= 1

        ack = receber(ws, "ack")
        assert ack["seq"] == 1
        assert ack["duplicate"] is False


def test_dois_clientes_no_mesmo_no(client: TestClient):
    ta, tb = token_de(client, "ana"), token_de(client, "bruno")
    with (
        client.websocket_connect(f"/ws?token={ta}") as a,
        client.websocket_connect(f"/ws?token={tb}") as b,
    ):
        a.receive_json()
        b.receive_json()
        a.send_json({"type": "join", "room": "geral", "last_seq": 0})
        a.receive_json()
        b.send_json({"type": "join", "room": "geral", "last_seq": 0})
        b.receive_json()
        a.receive_json()  # presence_update: bruno entrou

        b.send_json({"type": "send", "room": "geral", "content": "oi ana", "client_msg_id": "x1"})
        recebida = receber(a, "message")
        assert recebida["sender"] == "bruno"
        assert receber(b, "ack")["seq"] == 1


def test_envio_em_sala_nao_ocupada_e_recusado(client: TestClient):
    token = token_de(client, "ana")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()
        ws.send_json({"type": "send", "room": "alheia", "content": "x", "client_msg_id": "y1"})
        assert receber(ws, "error")["code"] == "not_in_room"


def test_frame_fora_do_protocolo_e_recusado(client: TestClient):
    token = token_de(client, "ana")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()
        ws.send_text('{"type": "comando_inexistente"}')
        assert receber(ws, "error")["code"] == "invalid_message"

        # A conexão sobrevive ao frame inválido.
        ws.send_json({"type": "ping"})
        assert receber(ws, "pong")["node_id"] == "node-teste"


def test_mensagem_acima_do_limite_e_recusada(client: TestClient):
    token = token_de(client, "ana")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()
        ws.send_json({"type": "join", "room": "geral", "last_seq": 0})
        ws.receive_json()
        ws.send_json(
            {"type": "send", "room": "geral", "content": "x" * 5000, "client_msg_id": "big"}
        )
        # Pydantic barra na borda, antes de qualquer efeito colateral.
        assert receber(ws, "error")["code"] == "invalid_message"


def test_rate_limit_nao_derruba_a_conexao(client_com_limite_baixo: TestClient):
    """FR-13: o excedente é recusado, mas a sessão permanece utilizável."""
    client = client_com_limite_baixo
    token = token_de(client, "ana")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()
        ws.send_json({"type": "join", "room": "geral", "last_seq": 0})
        ws.receive_json()

        for i in range(30):
            ws.send_json(
                {
                    "type": "send",
                    "room": "geral",
                    "content": f"rajada-{i}",
                    "client_msg_id": f"r{i}",
                }
            )

        assert receber(ws, "error", limite=120)["code"] == "rate_limited"

        # A sessão continua utilizável — conter o abuso não derruba o usuário.
        ws.send_json({"type": "ping"})
        assert receber(ws, "pong", limite=120)["node_id"] == "node-teste"


def test_sair_de_sala_mantem_a_conexao(client: TestClient):
    token = token_de(client, "ana")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()
        ws.send_json({"type": "join", "room": "a", "last_seq": 0})
        ws.receive_json()
        ws.send_json({"type": "join", "room": "b", "last_seq": 0})
        ws.receive_json()

        ws.send_json({"type": "leave", "room": "a"})
        assert receber(ws, "left")["room"] == "a"

        ws.send_json({"type": "send", "room": "b", "content": "ainda aqui", "client_msg_id": "z"})
        assert receber(ws, "ack")["room"] == "b"


# ---------------------------------------------------------------------------
# Histórico via HTTP (usado na demonstração para provar ausência de perda)
# ---------------------------------------------------------------------------


def test_historico_reporta_contiguidade(client: TestClient):
    token = token_de(client, "ana")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()
        ws.send_json({"type": "join", "room": "prova", "last_seq": 0})
        ws.receive_json()
        for i in range(5):
            ws.send_json(
                {
                    "type": "send",
                    "room": "prova",
                    "content": f"m{i}",
                    "client_msg_id": f"p{i}",
                }
            )
            receber(ws, "ack")

    body = client.get("/api/rooms/prova/messages").json()
    assert body["count"] == 5
    assert body["contiguous"] is True
    assert body["first_seq"] == 1
    assert body["last_seq"] == 5


def test_backlog_por_after_seq(client: TestClient):
    token = token_de(client, "ana")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()
        ws.send_json({"type": "join", "room": "hist", "last_seq": 0})
        ws.receive_json()
        for i in range(6):
            ws.send_json(
                {"type": "send", "room": "hist", "content": f"m{i}", "client_msg_id": f"h{i}"}
            )
            receber(ws, "ack")

    body = client.get("/api/rooms/hist/messages", params={"after_seq": 3}).json()
    assert [m["seq"] for m in body["messages"]] == [4, 5, 6]


def test_listagem_de_salas(client: TestClient):
    token = token_de(client, "ana")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()
        ws.send_json({"type": "join", "room": "visivel", "last_seq": 0})
        ws.receive_json()

        body = client.get("/api/rooms").json()
        salas = {r["room_id"] for r in body["rooms"]}
        assert "visivel" in salas
