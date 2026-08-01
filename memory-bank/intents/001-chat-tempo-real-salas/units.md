---
intent: 001-chat-tempo-real-salas
phase: inception
status: complete
updated: 2026-08-01T16:30:00Z
---

# Chat em Tempo Real com Salas - Unit Decomposition

## Units Overview

Este intent decompõe-se em **7 unidades** de trabalho.

O critério de corte entre unidades é a **direção da dependência da arquitetura hexagonal**: o núcleo puro primeiro, adaptadores depois, borda em seguida, e só então o que consome a borda. Isso permite que unidades sem dependência entre si sejam construídas em paralelo.

---

### Unit 1: 001-core-domain

**Description**: Núcleo puro do sistema — algoritmos e modelos sem nenhum I/O. Contém a implementação dos relógios lógicos (Lamport e vetorial), o envelope de mensagem, a fila de hold-back para reordenação e as interfaces (`Protocol`) que os adaptadores implementam. É a peça conceitualmente central do projeto: é aqui que mora o que a disciplina avalia.

**Stories**:
- S-006: Ordem total por número de sequência
- S-007: Reordenação de chegada fora de ordem (hold-back queue)
- S-008: Relógio de Lamport
- S-009: Relógio vetorial e detecção de concorrência

**Deliverables**:
- `src/salaviva/domain/clocks.py` — `LamportClock`, `VectorClock`
- `src/salaviva/domain/models.py` — `MessageEnvelope`, `Room`, `Member`
- `src/salaviva/domain/ordering.py` — `HoldBackQueue`
- `src/salaviva/domain/errors.py`
- `src/salaviva/ports.py` — `MessageBus`, `MessageRepository`, `PresenceStore`, `Sequencer`, `NodeRegistry`
- `src/salaviva/config.py`

**Dependencies**: Depends on: nenhuma · Depended by: todas as demais

**Estimated Complexity**: M

---

### Unit 2: 002-messaging-infra

**Description**: Adaptadores que implementam as portas do núcleo contra Redis e DynamoDB, mais as implementações em memória usadas nos testes e no modo standalone. Aqui vive a comunicação indireta propriamente dita.

**Stories**:
- S-004: Publicar mensagem em tópico da sala
- S-005: Assinar tópico e receber difusão
- S-010: Presença por sorted set com heartbeat
- S-012: Persistir mensagem no histórico
- S-013: Consultar backlog por `last_seq`
- S-016: Registro de nós vivos

**Deliverables**:
- `src/salaviva/infra/redis_bus.py` — Pub/Sub com reconexão e backoff exponencial + jitter
- `src/salaviva/infra/redis_sequencer.py` — `INCR` atômico
- `src/salaviva/infra/redis_presence.py` — `ZSET` + sweeper
- `src/salaviva/infra/redis_node_registry.py`
- `src/salaviva/infra/dynamo_repository.py`
- `src/salaviva/infra/memory/` — equivalentes em memória para teste

**Dependencies**: Depends on: 001-core-domain · Depended by: 003, 006

**Estimated Complexity**: L

---

### Unit 3: 003-websocket-gateway

**Description**: Borda do sistema. Aplicação FastAPI que termina as conexões WebSocket, valida o protocolo, autentica, aplica rate limit, orquestra o caso de uso de chat e expõe HTTP para health check, métricas e painel.

**Stories**:
- S-001: Conexão WebSocket persistente com heartbeat
- S-002: Entrar em sala
- S-003: Sair de sala
- S-011: Autenticação por JWT no handshake
- S-014: Idempotência por `client_msg_id`
- S-015: Endpoints de health e métricas
- S-019: Rate limiting por sessão

**Deliverables**:
- `src/salaviva/main.py` — composition root
- `src/salaviva/ws/protocol.py` · `connection.py` · `manager.py`
- `src/salaviva/app/chat_service.py` · `node_registry.py`
- `src/salaviva/api/auth.py` · `health.py` · `rooms.py`

**Dependencies**: Depends on: 001, 002 · Depended by: 004, 005, 006

**Estimated Complexity**: L

---

### Unit 4: 004-clients

**Description**: Os dois clientes. A UI web é o rosto da apresentação (Clareza vale 20 % da nota); o CLI é o instrumento que torna visíveis os relógios lógicos e a concorrência.

**Stories**:
- S-017: Cliente web com reconexão automática e indicador de nó
- S-018: Cliente CLI com visualização de `seq`/`lamport`/`node_id` e marcação de concorrência

**Deliverables**:
- `src/salaviva/static/index.html` · `app.js` · `style.css`
- `src/salaviva/static/dashboard.html` — painel de nós para a demo de falha
- `client/cli/salaviva_cli.py`

**Dependencies**: Depends on: 003 · Depended by: 007

**Estimated Complexity**: M

---

### Unit 5: 005-infrastructure

**Description**: Infraestrutura como código e empacotamento. Docker Compose reproduz o cluster localmente com paridade; Terraform provisiona a AWS; scripts automatizam deploy e a simulação de falha.

**Stories**:
- S-020: Imagem Docker e Compose com 3 nós + Redis + balanceador
- S-021: Terraform: VPC, ALB, ASG, ElastiCache, DynamoDB, ECR, IAM
- S-022: Scripts de deploy, teardown e simulação de falha

**Deliverables**:
- `Dockerfile`, `docker-compose.yml`, `nginx/nginx.conf`
- `infra/terraform/*.tf`
- `scripts/deploy.sh` · `teardown.sh` · `kill_node.sh` · `watch_cluster.sh`
- `Makefile`

**Dependencies**: Depends on: 003 · Depended by: 006 (e2e), 007

**Estimated Complexity**: L

---

### Unit 6: 006-quality

**Description**: Evidência de correção. Suíte de testes em três níveis mais teste de carga que gera os gráficos usados nos slides.

**Stories**:
- S-023: Testes unitários do domínio (relógios, hold-back, envelope)
- S-024: Testes de integração multi-nó (ordem total idêntica entre nós)
- S-025: Teste e2e de falha de nó sem perda de mensagem
- S-026: Teste de carga com ≥ 1.000 conexões e gráfico de latência

**Dependencies**: Depends on: 001, 002, 003, 005 · Depended by: 007

**Estimated Complexity**: L

---

### Unit 7: 007-deliverables

**Description**: Artefatos avaliados que não são código: o SDD (critério EC1), os slides no template exigido e o roteiro cronometrado da demonstração.

**Stories**:
- S-027: SDD com diagramas, ADRs e justificativa das escolhas AWS
- S-028: 10 slides conforme o template da disciplina
- S-029: Roteiro de demonstração de 15 min com script de falha

**Deliverables**:
- `docs/SDD.md`, `docs/protocolo.md`, `docs/diagramas/`
- `slides/apresentacao.md` (+ HTML renderizável e PPTX)
- `docs/roteiro-demo.md`

**Dependencies**: Depends on: 001–006 · Depended by: nenhuma

**Estimated Complexity**: M

---

## Unit Dependency Graph

```text
                    ┌──────────────────────┐
                    │  001-core-domain     │   (núcleo puro, sem I/O)
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  002-messaging-infra │   (Redis + DynamoDB)
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │ 003-websocket-gateway│   (FastAPI + WS)
                    └──────────┬───────────┘
                    ┌──────────┴───────────┐
                    ▼                      ▼
          ┌──────────────────┐   ┌──────────────────┐
          │  004-clients     │   │ 005-infrastructure│   (paralelo)
          └────────┬─────────┘   └─────────┬────────┘
                   └───────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                    │  006-quality         │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                    │  007-deliverables    │
                    └──────────────────────┘
```

## Execution Order

O caminho crítico é 001 → 002 → 003. As unidades 004 e 005 não dependem uma da outra e são construídas em paralelo assim que 003 estabiliza.

1. **Bolt 001** — `001-core-domain` (fundação; nada avança sem ela)
2. **Bolt 002** — `002-messaging-infra` (adaptadores)
3. **Bolt 003** — `003-websocket-gateway` (borda; fecha o sistema funcional)
4. **Bolts 004 e 005** — `004-clients` e `005-infrastructure` **em paralelo**
5. **Bolt 006** — `006-quality` (valida o conjunto)
6. **Bolt 007** — `007-deliverables` (documenta o que foi provado)
