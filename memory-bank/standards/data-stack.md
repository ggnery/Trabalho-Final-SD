---
standard: data-stack
status: approved
created: 2026-08-01T16:20:00Z
updated: 2026-08-01T16:20:00Z
---

# Data Stack

Database and data persistence decisions for this project.

---

## Database

**Decision**: dois armazenamentos com papéis distintos — **Redis (ElastiCache)** para estado volátil/coordenação e **DynamoDB** para histórico durável.

Esta separação é deliberada e é um dos pontos defendidos na arguição: estado de coordenação (quem está online, qual o próximo número de sequência) tem requisito de latência e não de durabilidade; histórico de mensagens tem requisito de durabilidade e não de latência.

### Redis — estado volátil e coordenação

| Chave | Tipo | Papel | TTL |
|---|---|---|---|
| `chat:room:{room_id}` | canal Pub/Sub | Tópico de fan-out da sala entre nós | — |
| `chat:seq:{room_id}` | String (contador) | Sequenciador de ordem total via `INCR` atômico | — |
| `chat:presence:{room_id}` | Sorted Set (score = epoch do último heartbeat) | Presença: membros online da sala | varrido por sweeper |
| `chat:session:{session_id}` | Hash | Mapa sessão → nó que a atende | 90 s (renovado) |
| `chat:nodes` | Sorted Set (score = epoch do último heartbeat) | Registro vivo de nós — alimenta o dashboard da demo de falha | varrido por sweeper |
| `chat:dedupe:{client_msg_id}` | String | Idempotência de envio (`SET NX`) | 300 s |

**Rationale**: Sorted Set com score temporal transforma expiração de presença em um `ZREMRANGEBYSCORE` O(log n) — não precisa de TTL por membro nem de varredura linear. Quando um nó morre, seus usuários somem da presença em até um ciclo de sweeper, sem que ninguém precise notificar a morte.

### DynamoDB — histórico durável

**Tabela `salaviva_messages`**

| Atributo | Tipo | Papel |
|---|---|---|
| `room_id` (PK) | S | Partition key — mensagens da mesma sala ficam co-localizadas |
| `seq` (SK) | N | Sort key — **ordem total dentro da sala, materializada no índice** |
| `message_id` | S | UUID da mensagem |
| `client_msg_id` | S | UUID do cliente, para idempotência |
| `sender` | S | Autor |
| `content` | S | Conteúdo |
| `lamport` | N | Relógio lógico de Lamport no momento do envio |
| `vector_clock` | M | Relógio vetorial (`node_id` → contador) |
| `node_id` | S | Nó que originou — evidência de distribuição na demo |
| `ts` | S | Timestamp físico ISO-8601 (**apenas informativo**, nunca usado para ordenar) |
| `ttl` | N | Expiração automática (7 dias) para conter custo |

**Rationale da chave**: `(room_id, seq)` faz o *replay* de reconexão ser uma única `Query` com `KeyConditionExpression = room_id = :r AND seq > :last`, já retornada em ordem crescente pelo próprio índice. Nenhuma ordenação em memória, nenhum `Scan`. É a razão de o `seq` ser sort key em vez de atributo comum.

**Tabela `salaviva_rooms`**

| Atributo | Tipo | Papel |
|---|---|---|
| `room_id` (PK) | S | Identificador da sala |
| `name` | S | Nome de exibição |
| `created_at` | S | Criação |

**Modo de capacidade**: On-Demand (`PAY_PER_REQUEST`). Sob Free Tier (25 GB + 25 WCU/RCU provisionadas) o on-demand evita ter que estimar pico de escrita, e a carga de uma demonstração é irregular por natureza.

---

## ORM / Database Client

**Decision**: sem ORM. `redis.asyncio` (redis-py 5.x) e `aioboto3` para DynamoDB, ambos atrás de interfaces de repositório definidas em `src/salaviva/ports.py`.

**Rationale**:
- Não há modelo relacional para mapear — o acesso é por chave em ambos os stores. Um ORM só adicionaria indireção.
- As interfaces (`MessageBus`, `MessageRepository`, `PresenceStore`, `Sequencer`) permitem que os testes rodem com implementações em memória, sem Redis nem DynamoDB Local. É o que torna a suíte de testes executável em qualquer máquina, incluindo a do avaliador.
- Clientes assíncronos são obrigatórios: um cliente bloqueante travaria o event loop e derrubaria todas as conexões WebSocket do nó a cada I/O.

---

## Consistency Model

| Dado | Modelo | Justificativa |
|---|---|---|
| Ordem das mensagens na sala | **Consistência forte** (ordem total via `INCR`) | O requisito funcional central: todos os clientes veem a mesma sequência. |
| Histórico em DynamoDB | Leitura eventualmente consistente por padrão; **fortemente consistente** no replay de reconexão | Replay precisa enxergar a última escrita, senão o cliente perde mensagem ao reconectar. |
| Lista de presença | **Consistência eventual** (janela ≤ 1 ciclo de sweeper, 15 s) | Ver um usuário fantasma por 15 s é irrelevante; pagar coordenação por isso não se justifica. Trade-off do Teorema CAP aplicado conscientemente: escolhemos AP para presença e CP para ordenação. |
