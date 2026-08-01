---
standard: coding-standards
status: approved
created: 2026-08-01T16:20:00Z
updated: 2026-08-01T16:20:00Z
---

# Coding Standards

Code style and quality standards for this project.

---

## Code Formatting

**Decision**: `ruff format` (compatível com Black), linha de 100 colunas.

**Rationale**: `ruff` faz formatação e lint em um único binário Rust, rodando em milissegundos. 100 colunas em vez de 88 porque as assinaturas assíncronas com type hints completos (`async def publish(self, room_id: str, envelope: MessageEnvelope) -> None:`) quebram feio em 88.

---

## Linting Rules

**Decision**: `ruff check` com `E,F,W,I,N,UP,B,ASYNC,S,C4,SIM,RUF` habilitados.

Destaque para dois grupos que importam neste projeto especificamente:
- **`ASYNC`** — pega chamada bloqueante dentro de corrotina. Em um servidor com milhares de WebSockets em um único event loop, um `time.sleep()` ou um cliente boto3 síncrono trava **todas** as conexões do nó. É a classe de bug mais perigosa aqui e o linter a pega estaticamente.
- **`S`** (bandit) — pega segredo hardcoded e uso inseguro de `random` para material criptográfico.

Exceções por diretório: `S101` (uso de `assert`) desabilitado em `tests/`.

---

## Naming Conventions

| Elemento | Convenção | Exemplo |
|---|---|---|
| Módulos / pacotes | `snake_case` | `redis_bus.py` |
| Classes | `PascalCase` | `ConnectionManager` |
| Funções / variáveis | `snake_case` | `broadcast_to_room` |
| Constantes | `UPPER_SNAKE_CASE` | `MAX_MESSAGE_LENGTH` |
| Interfaces (Protocol) | `PascalCase`, sem prefixo `I` | `MessageBus`, não `IMessageBus` |
| Chaves Redis | `chat:{recurso}:{id}` | `chat:room:geral` |
| Tipos de mensagem do protocolo | `snake_case` em string literal | `"presence_update"` |
| Métodos privados | prefixo `_` | `_sweep_stale_presence` |

Nomes de domínio em **inglês** no código (`Message`, `Room`, `LamportClock`) e documentação em **português**. Motivo: o vocabulário técnico de sistemas distribuídos é canônico em inglês na literatura (Lamport, Coulouris, Tanenbaum) e traduzir `happened-before` para `aconteceu-antes` no identificador prejudica a rastreabilidade com as referências citadas no SDD.

---

## File & Folder Organization

```
src/salaviva/
├── __init__.py
├── main.py              # composition root: monta FastAPI, injeta dependências
├── config.py            # Settings (pydantic-settings), lê env vars
├── ports.py             # Protocols: MessageBus, MessageRepository, PresenceStore, Sequencer
├── domain/              # LÓGICA PURA — zero I/O, zero import de redis/boto3
│   ├── clocks.py        #   LamportClock, VectorClock
│   ├── models.py        #   MessageEnvelope, Room, Member
│   ├── ordering.py      #   HoldBackQueue (reordenação por seq)
│   └── errors.py
├── infra/               # ADAPTADORES — implementam ports.py
│   ├── redis_bus.py
│   ├── redis_presence.py
│   ├── redis_sequencer.py
│   ├── dynamo_repository.py
│   └── memory/          #   implementações em memória (testes e modo standalone)
├── app/                 # CASOS DE USO — orquestra domain + ports
│   ├── chat_service.py
│   └── node_registry.py
├── ws/                  # BORDA WebSocket
│   ├── protocol.py      #   modelos Pydantic do protocolo cliente↔servidor
│   ├── connection.py    #   ClientConnection
│   └── manager.py       #   ConnectionManager
├── api/                 # BORDA HTTP
│   ├── auth.py
│   ├── health.py
│   └── rooms.py
└── static/              # cliente web
```

**Regra de dependência (arquitetura hexagonal)**: `domain/` não importa nada de `infra/`, `app/`, `ws/` ou de bibliotecas externas de I/O. A seta de dependência aponta sempre para dentro. Consequência concreta: `domain/clocks.py` — que contém o algoritmo de Lamport, a peça que o professor vai querer ver — é testável sem Redis, sem AWS, sem rede, em microssegundos. Um teste `pytest tests/unit/test_clocks.py` roda na máquina do avaliador sem nenhum setup.

---

## Testing Strategy

**Decision**: pirâmide em três níveis, `pytest` + `pytest-asyncio`, meta de cobertura ≥ 85 % em `domain/` e `app/`.

| Nível | Escopo | Dependências | Onde roda |
|---|---|---|---|
| **Unitário** (`tests/unit/`) | `domain/` puro: Lamport, vetorial, hold-back queue, serialização | Nenhuma | Sempre, em qualquer máquina |
| **Integração** (`tests/integration/`) | `app/` + adaptadores em memória; multi-nó simulado no mesmo processo | `ports.py` fake | Sempre |
| **End-to-end** (`tests/e2e/`) | 3 nós reais + Redis real via Docker Compose, clientes WebSocket reais | Docker | `make test-e2e` |
| **Carga** (`loadtest/`) | N conexões WebSocket concorrentes, mede latência p50/p95/p99 e ordem | Cluster ativo | Manual, gera gráfico para os slides |

**Testes obrigatórios de correção distribuída** (são a evidência dos critérios EC2):
1. `test_lamport_monotonic` — o relógio nunca regride.
2. `test_lamport_happened_before` — se `a → b`, então `L(a) < L(b)`.
3. `test_vector_clock_detects_concurrency` — eventos concorrentes são reconhecidos como incomparáveis.
4. `test_total_order_identical_across_nodes` — **3 nós, 200 mensagens intercaladas, todos entregam a mesma sequência**. É o teste que prova o requisito central do Projeto 3.
5. `test_holdback_queue_reorders_out_of_order_arrival` — chegada 3,1,2 é entregue como 1,2,3.
6. `test_no_message_loss_on_node_failure` — nó cai no meio do fluxo, cliente reconecta com `last_seq` e recebe exatamente o gap.
7. `test_duplicate_client_msg_id_is_idempotent` — reenvio não duplica.

---

## Error Handling Patterns

**Decision**: exceções de domínio tipadas em `domain/errors.py`; a borda traduz para o protocolo; falha de dependência **degrada**, não derruba.

```python
# domain/errors.py
class SalaVivaError(Exception): ...
class RoomNotFound(SalaVivaError): ...
class MessageTooLong(SalaVivaError): ...
class RateLimitExceeded(SalaVivaError): ...
class AuthenticationFailed(SalaVivaError): ...
```

Regras:
1. **Nunca `except: pass`.** Todo `except` ou trata, ou re-levanta, ou loga com contexto estruturado.
2. **Uma conexão que falha não derruba o nó.** Cada `ClientConnection` roda em `asyncio.Task` própria com `try/except` no topo; erro fecha aquela conexão com código WebSocket apropriado e segue.
3. **Falha de dependência degrada com clareza**:
   - Redis fora → `/readyz` passa a falhar → ALB tira o nó do pool → clientes migram. O nó **não** entra em pânico nem se mata.
   - DynamoDB fora → mensagens continuam sendo entregues em tempo real (Pub/Sub não depende dele); só o replay de histórico fica indisponível. Registra-se o erro e segue. **A entrega em tempo real jamais bloqueia por persistência.**
4. **Reconexão ao Redis com backoff exponencial + jitter** (0,5 s → 30 s). O jitter evita que os N nós do ASG reconectem em sincronia e criem um *thundering herd* no Redis logo após uma partição — o modo de falha em que uma recuperação parcial vira uma queda total.

---

## Logging Standards

**Decision**: log estruturado em JSON (`structlog`) para `stdout`, coletado pelo CloudWatch Agent.

Campos obrigatórios em todo evento: `ts`, `level`, `event`, `node_id`.
Campos contextuais quando aplicável: `room_id`, `session_id`, `user`, `seq`, `lamport`, `latency_ms`.

```json
{"ts":"2026-08-01T16:20:03.412Z","level":"info","event":"message_published",
 "node_id":"node-a3f2","room_id":"geral","seq":142,"lamport":87,"latency_ms":2.1}
```

**Rationale**: `node_id` em toda linha é inegociável neste projeto. Na demonstração de falha, a evidência de que o sistema se recuperou é ver, no CloudWatch Logs Insights, as mensagens migrarem de `node_id: node-a3f2` para `node_id: node-b71c` no instante em que a instância foi derrubada — sem gap na sequência de `seq`. JSON estruturado torna isso uma query (`stats count(*) by node_id`) em vez de leitura de texto corrido durante a apresentação.

Níveis: `debug` (desenvolvimento), `info` (ciclo de vida: conexão, publicação, entrada/saída de sala), `warning` (degradação recuperável: reconexão, rate limit), `error` (falha que afeta o usuário).
