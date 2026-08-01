# Protocolo SalaViva (WebSocket)

Contrato entre cliente e servidor. A definição executável está em
`src/salaviva/ws/protocol.py`, validada por Pydantic v2 — este documento é a
versão legível.

Todo frame é um objeto JSON com o campo discriminador `type`.

---

## Handshake

```
GET /ws?token=<JWT>
```

O token é obtido em `POST /auth/login` com `{"username": "..."}`.

A validação acontece **antes** do `accept`: um token ausente, inválido ou
expirado fecha a conexão com o código **4401** e nunca chega a consumir um slot
no nó.

Aceito o handshake, o servidor envia imediatamente:

```json
{ "type": "welcome", "node_id": "node-a3f2", "session_id": "8c1e…",
  "user": "gabriel", "server_time": "2026-08-01T16:20:03.412Z" }
```

O `node_id` é o que permite ao cliente exibir a qual instância está conectado —
e é o que torna visível, na demonstração, a migração dos clientes quando um nó
é derrubado.

---

## Cliente → Servidor

### `join` — entrar em uma sala

```json
{ "type": "join", "room": "geral", "last_seq": 0 }
```

`last_seq` é o mecanismo de recuperação sem perda: informe o último `seq`
renderizado e receba de volta exatamente o que faltou. Na primeira conexão, `0`.

### `leave` — sair de uma sala

```json
{ "type": "leave", "room": "geral" }
```

A conexão permanece aberta para as demais salas.

### `send` — publicar uma mensagem

```json
{ "type": "send", "room": "geral", "content": "olá",
  "client_msg_id": "a1b2c3d4" }
```

`client_msg_id` é gerado pelo cliente (UUID). Reenviar com o mesmo valor é
seguro: o servidor devolve o `ack` original em vez de criar uma nova mensagem
(FR-9).

Enviar para uma sala em que a sessão não entrou devolve `error/not_in_room`.

### `resync` — pedir o backlog

```json
{ "type": "resync", "room": "geral", "after_seq": 42 }
```

Emitido quando a fila de hold-back detecta uma lacuna que não se fecha — o caso
em que o Pub/Sub *at-most-once* perdeu uma mensagem para aquele nó.

### `typing` / `ping`

```json
{ "type": "typing", "room": "geral" }
{ "type": "ping" }
```

---

## Servidor → Cliente

### `joined`

```json
{ "type": "joined", "room": "geral", "node_id": "node-a3f2", "last_seq": 142,
  "members": [ { "user": "ana", "session_id": "…", "node_id": "node-b71c",
                 "joined_at": "…" } ],
  "backlog": [ /* MessageEnvelope, em ordem crescente de seq */ ] }
```

Repare que `members` traz o `node_id` de cada membro: é a evidência, na própria
UI, de que participantes da mesma sala estão distribuídos entre instâncias
diferentes.

### `message` — o envelope canônico

```json
{ "type": "message",
  "message_id": "7f3a…",
  "client_msg_id": "a1b2c3d4",
  "room_id": "geral",
  "sender": "gabriel",
  "session_id": "8c1e…",
  "content": "olá",
  "seq": 143,
  "lamport": 87,
  "vector_clock": { "node-a3f2": 45, "node-b71c": 42 },
  "node_id": "node-a3f2",
  "ts": "2026-08-01T16:20:03.412Z" }
```

Os três campos de ordenação, e o papel de cada um:

| Campo | Papel | Usar para ordenar? |
|---|---|---|
| `seq` | Ordem **total** da sala, de um `INCR` atômico | **Sim — é o único** |
| `lamport` | Relação *happened-before* entre eventos | Não (ordem apenas parcial) |
| `vector_clock` | Detecção de **concorrência** | Não (é diagnóstico) |
| `ts` | Timestamp físico | **Nunca** (relógios de EC2 divergem) |

### `ack`

```json
{ "type": "ack", "client_msg_id": "a1b2c3d4", "room": "geral",
  "seq": 143, "lamport": 87, "duplicate": false }
```

Chega **antes** do eco da mensagem pelo Pub/Sub. É o que permite à UI marcar a
mensagem como enviada sem esperar o round-trip ao broker, tornando imperceptível
o custo de latência da decisão do ADR-004.

`duplicate: true` indica que o `client_msg_id` já havia sido processado. Se além
disso `seq` vier `0`, o envio original ainda estava em voo; o cliente reconcilia
quando o eco chegar.

### `presence_update`

```json
{ "type": "presence_update", "room": "geral", "event": "join",
  "user": "ana", "members": [ … ] }
```

### `pong` · `typing` · `left`

```json
{ "type": "pong", "server_time": "…", "node_id": "node-a3f2" }
{ "type": "typing", "room": "geral", "user": "ana" }
{ "type": "left", "room": "geral" }
```

O servidor envia `pong` espontaneamente a cada 20 s como heartbeat — não é
apenas resposta ao `ping` do cliente. Dois heartbeats sem resposta encerram a
conexão.

### `error`

```json
{ "type": "error", "code": "rate_limited", "message": "…" }
```

| `code` | Significado | Reação esperada do cliente |
|---|---|---|
| `auth_failed` | Token inválido/expirado (fecha com 4401) | Reautenticar |
| `invalid_message` | Frame fora do protocolo | Corrigir (é bug do cliente) |
| `not_in_room` | Enviou para sala não ocupada | Fazer `join` antes |
| `message_too_long` | Acima de 4096 caracteres | Encurtar |
| `rate_limited` | Acima de 20 msg/s na sessão | Backoff; a conexão **não** é fechada |
| `service_unavailable` | Nó degradado (dependência caiu) | Tentar de novo; o ALB vai remover o nó |

---

## Algoritmo do cliente

Ordem correta de exibição, na sequência exata:

1. Ao conectar, envie `join` com o `last_seq` conhecido (`0` na primeira vez).
2. Renderize o `backlog` de `joined` em ordem — ele já vem ordenado por `seq`.
3. Alimente **toda** mensagem recebida em uma fila de hold-back
   (`domain/ordering.py`) inicializada com `last_seq`.
4. Renderize apenas o que a fila liberar. Ela garante contiguidade e descarta
   duplicatas — o que torna seguro o backlog sobrepor mensagens já recebidas em
   tempo real.
5. Se a lacuna persistir por mais de ~2 s, envie `resync` com o `last_seq` atual.
6. Ao perder a conexão, reconecte com backoff e refaça o `join` com o `last_seq`
   atualizado. **É este passo que garante zero perda quando um nó cai.**

---

## Códigos de fechamento

| Código | Motivo |
|---|---|
| 1000 | Encerramento normal |
| 1001 | Nó encerrando ou heartbeat falhou |
| 1011 | Erro interno |
| **4401** | Falha de autenticação (faixa privada da aplicação) |
