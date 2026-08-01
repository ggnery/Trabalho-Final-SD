---
standard: system-architecture
status: approved
created: 2026-08-01T16:20:00Z
updated: 2026-08-01T16:20:00Z
---

# System Architecture

High-level architectural decisions and patterns.

---

## Architecture Style

**Decision**: Cluster de nós stateless-por-fora / stateful-por-dentro, coordenados por **comunicação indireta publish-subscribe**. Arquitetura orientada a eventos, sem comunicação nó-a-nó.

```
                    ┌──────────────────┐
                    │  Cliente Web /   │
                    │   Cliente CLI    │
                    └────────┬─────────┘
                             │ WebSocket (wss)
                             ▼
                 ┌───────────────────────┐
                 │  Application Load     │   health check ativo:
                 │  Balancer (2 AZs)     │   GET /readyz a cada 15s
                 └───────────┬───────────┘
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │  Nó A    │   │  Nó B    │   │  Nó C    │   Auto Scaling Group
        │ EC2      │   │ EC2      │   │ EC2      │   (min 2, desired 3, max 4)
        │ Lamport  │   │ Lamport  │   │ Lamport  │
        │ L=17     │   │ L=17     │   │ L=17     │
        └────┬─────┘   └────┬─────┘   └────┬─────┘
             │              │              │
             └──────────────┼──────────────┘
                            ▼
              ┌─────────────────────────────┐
              │   ElastiCache for Redis     │
              │  ─────────────────────────  │
              │  PUB/SUB  chat:room:{id}    │  ← fan-out (comunicação em grupo)
              │  INCR     chat:seq:{id}     │  ← sequenciador de ordem total
              │  ZSET     chat:presence:{id}│  ← presença
              │  ZSET     chat:nodes        │  ← registro de nós vivos
              └─────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────────┐
              │   DynamoDB                  │
              │   (room_id, seq) → mensagem │  ← histórico + replay de reconexão
              └─────────────────────────────┘
```

**Propriedade central**: **nenhum nó conhece nenhum outro nó**. Um nó só conhece o Redis. Isso é comunicação indireta no sentido estrito de Coulouris (§6): emissor e receptor desacoplados no espaço (não sabem a identidade um do outro) e no tempo (não precisam estar ativos simultaneamente para o canal existir). A consequência prática é que adicionar ou remover um nó não exige reconfigurar nenhum outro — é o que faz o Auto Scaling funcionar sem orquestração.

---

## Fluxo de uma mensagem (caminho crítico)

```
1. Cliente ──"send"──► Nó A (WebSocket)
2. Nó A: valida JWT + protocolo (Pydantic)
3. Nó A: SET NX chat:dedupe:{client_msg_id}  ─── se já existe, descarta (idempotência)
4. Nó A: lamport.tick()                      ─── L_A = L_A + 1
5. Nó A: seq = INCR chat:seq:{room}          ─── ordem total atômica
6. Nó A: PutItem DynamoDB (assíncrono, não bloqueia)
7. Nó A: PUBLISH chat:room:{room} <envelope>
8. Redis ──fan-out──► Nós A, B e C (todos inscritos, INCLUSIVE o emissor)
9. Cada nó: lamport.update(env.lamport)      ─── L = max(L, L_recebido) + 1
10. Cada nó: entrega aos seus WebSockets locais daquela sala
11. Cliente: hold-back queue reordena por seq antes de renderizar
```

**Decisão de projeto notável (passo 8)**: o nó emissor **não** entrega a mensagem localmente no passo 7 — ele espera ela voltar pelo Pub/Sub como qualquer outro nó. Isso custa um round-trip ao Redis, mas garante que existe **um único caminho de entrega** no sistema. Se o emissor entregasse localmente por atalho, clientes do nó A veriam a mensagem em ordem de chegada local e clientes do nó B em ordem de `seq` — dois caminhos, duas ordens possíveis, e a garantia de ordenação total viraria uma garantia condicional. O `ack` imediato ao remetente (passo 5) cobre a percepção de latência na UI.

---

## Ordenação de Eventos

Este é o requisito acadêmico central do Projeto 3. O sistema implementa **três mecanismos complementares**, e a distinção entre eles é o que se defende na arguição:

### 1. Relógio Lógico de Lamport — relação *happened-before* (→)

Cada nó mantém um contador `L`. Regras (Lamport, 1978):
- Evento local / envio: `L := L + 1`
- Recebimento de mensagem com relógio `L_msg`: `L := max(L, L_msg) + 1`

Garante: se `a → b` (a causou b), então `L(a) < L(b)`.
**Não** garante a recíproca: `L(a) < L(b)` não implica `a → b` — podem ser concorrentes. Essa é a limitação conhecida do relógio escalar.

### 2. Relógio Vetorial — detecção de concorrência

Cada nó mantém `V[node_id] → contador` para todos os nós conhecidos. Permite decidir, para quaisquer dois eventos:
- `V(a) < V(b)` → `a` aconteceu antes de `b`
- `V(a) ∥ V(b)` (incomparáveis) → **eventos concorrentes**

Usado para *diagnóstico*: o cliente CLI exibe `⚡ concorrente` quando detecta mensagens sem relação causal. É a evidência visual, na demo, de que o sistema é genuinamente distribuído — mensagens concorrentes só existem quando há mais de um nó originando eventos.

### 3. Sequenciador Total (`INCR` no Redis) — ordem de entrega

Lamport dá ordem *parcial*; a UI precisa de ordem *total* (uma lista linear). O `INCR` atômico por sala produz um `seq` monotônico e único. Todos os clientes ordenam por `seq` e, portanto, **veem exatamente a mesma sequência**.

**Trade-off assumido e defendido**: o sequenciador é um ponto de serialização por sala. Sacrificamos escalabilidade de escrita *dentro de uma sala* em troca de ordem total determinística. Como as salas são independentes, o sistema continua escalando horizontalmente no número de salas — que é a dimensão que de fato cresce em um chat. Alternativas descartadas: ordenação por timestamp físico (relógios de EC2 divergem, mesmo com NTP → mensagens fora de ordem) e consenso tipo Raft (custo de coordenação injustificável para o requisito).

---

## State Management

| Estado | Onde vive | O que acontece se o nó morrer |
|---|---|---|
| Conexões WebSocket ativas | Memória do nó (`dict`) | Perdidas — clientes reconectam via ALB em outro nó |
| Relógio de Lamport / vetorial | Memória do nó | Reinicia em 0; converge em uma mensagem via `max()` |
| Assinaturas Pub/Sub | Conexão Redis do nó | Redis limpa ao detectar socket morto |
| Presença | Redis (ZSET) | Varrida pelo sweeper em ≤ 15 s |
| Número de sequência | Redis | **Intacto** — ordem nunca regride |
| Histórico | DynamoDB | **Intacto** — replay recompõe a sala |

**Consequência para a demo de falha (EC3)**: derrubar um nó custa aos seus clientes uma reconexão (~2 s) e **zero mensagens**, porque `seq` e histórico vivem fora do nó. O cliente reconecta enviando `last_seq` e recebe o backlog exato que perdeu. Isso é tolerância a falhas demonstrável, não afirmada.

---

## Caching Strategy

**Decision**: sem cache de leitura de mensagens. Backlog de reconexão é lido direto do DynamoDB com leitura fortemente consistente.

**Rationale**: cachear o backlog introduziria uma janela em que o cliente reconectado recebe histórico obsoleto — exatamente o cenário que a reconexão existe para evitar. O volume é baixo (últimas N mensagens de uma sala) e o custo de latência não aparece na percepção do usuário, que já está em um evento de reconexão. Cache aqui trocaria correção por uma otimização que ninguém percebe.

---

## Security Patterns

| Camada | Controle |
|---|---|
| Transporte | TLS no ALB (HTTPS/WSS); tráfego interno confinado à VPC |
| Autenticação | JWT HS256, expiração de 12 h, validado no handshake do WebSocket |
| Autorização | `sub` do token vincula a sessão ao usuário; um cliente não pode publicar como outro |
| Rede | Security Groups em cadeia: ALB aceita 80/443 da internet → EC2 aceita 8000 **apenas do SG do ALB** → Redis aceita 6379 **apenas do SG do EC2**. Nem Redis nem EC2 têm porta de aplicação exposta à internet. |
| Segredos | JWT secret via SSM Parameter Store (`SecureString`), lido no boot pelo instance profile — nunca em `user_data` nem na imagem |
| Rate limiting | Token bucket por sessão (20 msg/s) no nó, defesa contra cliente abusivo |
| Validação | Pydantic v2 rejeita payload malformado antes de qualquer efeito colateral |
| IAM | Instance profile com política de menor privilégio: apenas `dynamodb:PutItem/Query` nas duas tabelas e `ssm:GetParameter` no parâmetro do segredo |

---

## API Design

| Endpoint | Método | Papel |
|---|---|---|
| `/` | GET | Cliente web (HTML estático servido pelo próprio nó) |
| `/dashboard` | GET | Painel de nós vivos — usado na demonstração de falha |
| `/auth/login` | POST | Emite JWT |
| `/ws` | WebSocket | Canal de chat (protocolo JSON documentado em `docs/protocolo.md`) |
| `/healthz` | GET | Liveness — responde se o processo está vivo |
| `/readyz` | GET | Readiness — verifica Redis e DynamoDB; **é este que o ALB consulta** |
| `/metrics` | GET | Métricas JSON do nó (conexões, salas, msgs/s, `node_id`, uptime) |
| `/api/rooms` | GET | Lista salas com contagem de membros |

**Rationale da separação `/healthz` vs `/readyz`**: se o ALB consultasse `/healthz`, um nó que perdeu a conexão com o Redis continuaria "saudável" e receberia tráfego que não consegue servir — um buraco negro. `/readyz` verifica as dependências, então o nó degradado se autoexclui do pool. Essa distinção é o que faz o failover ser automático em vez de manual.
