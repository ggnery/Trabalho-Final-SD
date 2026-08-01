---
intent: 001-chat-tempo-real-salas
phase: inception
status: complete
created: 2026-08-01T16:25:00Z
updated: 2026-08-01T16:25:00Z
---

# Requirements: Chat em Tempo Real com Salas (Pub/Sub)

## Intent Overview

Construir o **SalaViva**: um aplicativo de mensagens instantâneas com suporte a múltiplas salas, distribuído horizontalmente na AWS, capaz de suportar milhares de conexões WebSocket simultâneas e de garantir que **todos os participantes de uma sala vejam as mensagens exatamente na mesma ordem**, mesmo estando conectados a nós físicos diferentes.

O sistema é o artefato avaliado do Projeto 3 da disciplina de Sistemas Distribuídos. Sua razão de existir é dupla: funcionar como software e **evidenciar concretamente** os conceitos de comunicação em grupo, comunicação indireta, ordenação de eventos por relógios lógicos, concorrência, escalabilidade horizontal e tolerância a falhas.

## Business Goals

| Goal | Success Metric | Priority |
|------|----------------|----------|
| Entregar chat multi-sala funcional na nuvem | Demo ao vivo na AWS com ≥ 2 nós e ≥ 3 clientes simultâneos | Must |
| Provar ordenação total de mensagens | Teste automatizado: 3 nós × 200 mensagens intercaladas → sequência idêntica em todos os clientes | Must |
| Provar tolerância a falhas | Instância EC2 derrubada ao vivo → zero mensagens perdidas, ASG recria o nó, clientes reconectam | Must |
| Provar escalabilidade | Teste de carga com ≥ 1.000 conexões WebSocket concorrentes, p95 < 200 ms | Should |
| Documentação arquitetural defensável (SDD) | SDD com diagramas, ADRs e justificativa de cada escolha AWS | Must |
| Apresentação de 15 min no template exigido | 10 slides conforme `Modelo_Apresentacao_Projeto_Disciplina.pdf` | Must |

---

## Functional Requirements

### FR-1: Autenticação de usuário
- **Description**: O usuário obtém um token JWT informando um nome de usuário e apresenta esse token no handshake do WebSocket.
- **Acceptance Criteria**: `POST /auth/login` com `username` válido retorna JWT HS256 com `sub` e `exp`. Conexão WebSocket sem token ou com assinatura inválida é rejeitada com código de fechamento `4401`. Token expirado é rejeitado.
- **Priority**: Must
- **Related Stories**: S-011

### FR-2: Conexão WebSocket persistente
- **Description**: O cliente estabelece uma conexão WebSocket com qualquer nó do cluster e a mantém aberta, recebendo eventos em push.
- **Acceptance Criteria**: Conexão em `/ws?token=…` é aceita e mantida viva por ≥ 30 min com heartbeat ping/pong a cada 20 s. Queda de rede fecha a conexão em ≤ 40 s (dois pings perdidos).
- **Priority**: Must
- **Related Stories**: S-001

### FR-3: Entrar e sair de salas
- **Description**: Um usuário pode participar de múltiplas salas simultaneamente na mesma conexão, entrando e saindo dinamicamente.
- **Acceptance Criteria**: `join` em sala inexistente cria a sala. `join` retorna a lista de membros e o backlog de mensagens. `leave` remove o usuário da presença. Um mesmo socket pode estar em ≥ 5 salas ao mesmo tempo.
- **Priority**: Must
- **Related Stories**: S-002, S-003

### FR-4: Envio e difusão de mensagens (comunicação em grupo)
- **Description**: Mensagem enviada por um membro é entregue a **todos** os membros da sala, independentemente do nó ao qual cada um esteja conectado.
- **Acceptance Criteria**: Com 3 nós ativos e 1 cliente em cada, mensagem enviada por qualquer cliente chega aos outros 2 em < 200 ms (p95). Nenhum membro da sala deixa de receber. Membros de outras salas não recebem.
- **Priority**: Must
- **Related Stories**: S-004, S-005

### FR-5: Ordenação total das mensagens
- **Description**: Toda mensagem recebe um número de sequência monotônico e único por sala. Todos os clientes exibem as mensagens na mesma ordem.
- **Acceptance Criteria**: Para qualquer par de clientes na mesma sala, a subsequência de mensagens recebidas por ambos é idêntica. Números de sequência de uma sala são contíguos e sem repetição. Chegada fora de ordem é reordenada pelo cliente antes da exibição (hold-back queue).
- **Priority**: Must
- **Related Stories**: S-006, S-007

### FR-6: Relógios lógicos
- **Description**: Cada nó mantém um relógio de Lamport e um relógio vetorial, ambos transportados no envelope da mensagem.
- **Acceptance Criteria**: O relógio de Lamport nunca regride. Se a mensagem `a` causou a mensagem `b`, então `L(a) < L(b)`. O relógio vetorial identifica corretamente pares de mensagens concorrentes, e o cliente CLI as sinaliza visualmente.
- **Priority**: Must
- **Related Stories**: S-008, S-009

### FR-7: Presença (lista de participantes)
- **Description**: Cada sala mantém a lista de quem está online, atualizada em tempo real para todos os membros.
- **Acceptance Criteria**: Entrada/saída de um usuário gera evento `presence_update` a todos os membros da sala em < 1 s. Usuário cuja conexão morreu sem `leave` desaparece da lista em ≤ 15 s (sweeper).
- **Priority**: Must
- **Related Stories**: S-010

### FR-8: Histórico e replay na reconexão
- **Description**: Mensagens são persistidas; ao reconectar, o cliente informa o último `seq` visto e recebe exatamente as mensagens perdidas.
- **Acceptance Criteria**: `join` com `last_seq = N` retorna todas as mensagens da sala com `seq > N`, em ordem crescente. Após derrubar um nó com clientes ativos, os clientes reconectados exibem a sala completa, sem lacuna e sem duplicata.
- **Priority**: Must
- **Related Stories**: S-012, S-013

### FR-9: Idempotência de envio
- **Description**: Reenvio da mesma mensagem (mesmo `client_msg_id`) não a duplica na sala.
- **Acceptance Criteria**: Dois `send` com `client_msg_id` idêntico dentro de 5 min produzem exatamente uma mensagem com um único `seq`.
- **Priority**: Should
- **Related Stories**: S-014

### FR-10: Observabilidade e painel de nós
- **Description**: O sistema expõe métricas por nó e um painel web que mostra os nós vivos e sua carga.
- **Acceptance Criteria**: `GET /metrics` retorna `node_id`, conexões ativas, salas ativas, mensagens publicadas/recebidas, uptime. `GET /dashboard` lista todos os nós registrados no Redis com heartbeat < 15 s. Ao derrubar um nó, ele desaparece do painel em ≤ 15 s.
- **Priority**: Must
- **Related Stories**: S-015, S-016

### FR-11: Cliente web
- **Description**: Interface web para a demonstração: login, seleção de sala, envio e recepção de mensagens, lista de presença, indicador do nó servidor.
- **Acceptance Criteria**: Funciona sem etapa de build, servida pelo próprio backend. Exibe `node_id` que atende a conexão e reconecta automaticamente com backoff ao perder a conexão.
- **Priority**: Must
- **Related Stories**: S-017

### FR-12: Cliente CLI
- **Description**: Cliente de terminal para evidenciar ordenação, relógios lógicos e concorrência.
- **Acceptance Criteria**: Exibe, por mensagem, `seq`, `lamport`, `node_id` e marca `⚡` quando o relógio vetorial indica concorrência. Suporta modo `--observer` (somente leitura) para projetar durante a apresentação.
- **Priority**: Should
- **Related Stories**: S-018

### FR-13: Rate limiting
- **Description**: Uma sessão abusiva não pode degradar o serviço das demais.
- **Acceptance Criteria**: Acima de 20 mensagens/s por sessão, o excedente é rejeitado com `error/rate_limited` sem fechar a conexão nem afetar outras sessões.
- **Priority**: Should
- **Related Stories**: S-019

---

## Non-Functional Requirements

### Performance
| Requirement | Metric | Target |
|-------------|--------|--------|
| Latência de entrega fim a fim (mesma sala, nós distintos) | p95 | < 200 ms |
| Latência de entrega fim a fim | p99 | < 500 ms |
| Overhead do sequenciador (`INCR`) | p95 | < 5 ms |
| Throughput por nó | mensagens/s | ≥ 500 |
| Tempo de handshake WebSocket (com validação de JWT) | p95 | < 300 ms |

### Scalability
| Requirement | Metric | Target |
|-------------|--------|--------|
| Conexões WebSocket concorrentes por nó `t3.micro` | Sockets abertos | ≥ 500 |
| Conexões concorrentes no cluster (3 nós) | Sockets abertos | ≥ 1.500 |
| Salas simultâneas ativas | Salas | ≥ 100 |
| Escala horizontal | Adicionar nó | Sem reconfigurar nós existentes (zero acoplamento nó-a-nó) |

### Security
| Requirement | Standard | Notes |
|-------------|----------|-------|
| Autenticação | JWT HS256, exp 12 h | Validado no handshake; segredo em SSM Parameter Store (`SecureString`) |
| Autorização | Vínculo `sub` → sessão | Cliente não pode publicar sob identidade alheia |
| Transporte | TLS 1.2+ no ALB | WSS externo; tráfego interno confinado à VPC |
| Isolamento de rede | Security Groups encadeados | EC2 aceita 8000 só do SG do ALB; Redis aceita 6379 só do SG do EC2 |
| Validação de entrada | Pydantic v2 | Payload malformado rejeitado antes de efeito colateral |
| Menor privilégio | IAM instance profile | Apenas `dynamodb:PutItem/Query` nas 2 tabelas + `ssm:GetParameter` |
| Limite de payload | 4 KB por mensagem | Rejeita `MessageTooLong` |

### Reliability
| Requirement | Metric | Target |
|-------------|--------|--------|
| Perda de mensagens ao derrubar um nó | Mensagens perdidas | **0** (garantido por `seq` no Redis + histórico no DynamoDB) |
| Tempo de reconexão do cliente após queda do nó | p95 | < 5 s |
| Tempo para o ASG restaurar a capacidade desejada | Novo nó `InService` | < 3 min |
| Detecção de nó doente pelo ALB | Health check | ≤ 45 s (3 falhas × 15 s em `/readyz`) |
| Remoção de presença fantasma | Sweeper | ≤ 15 s |
| Disponibilidade durante a falha de 1 nó (de 3) | Capacidade | ≥ 66 % imediata, 100 % após ASG |

### Compliance
| Requirement | Standard | Notes |
|-------------|----------|-------|
| Custo dentro do Free Tier | AWS Free Tier (12 meses) | `t3.micro` ×3, ELB 750 h, DynamoDB on-demand, `cache.t3.micro`. Sem NAT Gateway (ADR-006). `terraform destroy` documentado. |
| Retenção de dados | TTL de 7 dias no DynamoDB | Contenção de custo e de volume |

---

## Constraints

### Technical Constraints

**Project-wide standards**: carregados de `memory-bank/standards/` pelo Construction Agent (tech-stack, data-stack, coding-standards, system-architecture).

**Intent-specific constraints**:
- Backend **obrigatoriamente em Python** (decisão do time; registrada em tech-stack).
- A demonstração precisa de uma instância EC2 derrubável ao vivo — restrição que descarta arquitetura puramente serverless (ADR-001).
- Cliente web sem etapa de build: o avaliador não deve precisar rodar `npm install`.
- O projeto deve rodar integralmente em Docker Compose local, com paridade de comportamento, para ensaio e para o caso de falha de rede/AWS no dia da apresentação.
- Relógio físico **não pode** ser usado para ordenar mensagens (ADR-003).

### Business Constraints
- Prazo: apresentação de 15 min + 5 min de arguição.
- Orçamento: Free Tier da AWS; nenhum recurso fora dele sem justificativa explícita.
- Equipe: todos os integrantes devem apresentar (exigência do template de slides).

---

## Assumptions

| Assumption | Risk if Invalid | Mitigation |
|------------|-----------------|------------|
| A conta AWS tem Free Tier ativo e limite para 3 `t3.micro` + 1 ElastiCache | Custo inesperado ou falha de provisionamento | `terraform plan` revisado antes do apply; `terraform destroy` após a apresentação; ASG com `max_size = 4` |
| Haverá rede estável no local da apresentação | Demo ao vivo na AWS impossível | Docker Compose local com paridade total + vídeo de backup da demo de falha gravado previamente |
| Nós do cluster confiam uns nos outros (sem adversário interno) | Modelo de segurança insuficiente | Isolamento por Security Group na VPC; falhas bizantinas declaradas fora de escopo no SDD |
| ElastiCache single-node é suficiente | Ponto único de falha derruba o chat | Limitação declarada no SDD e no slide de Conclusão; mitigação de produção (Multi-AZ com réplica) descrita |
| Volume da demo não excede o Free Tier do DynamoDB | Cobrança | On-demand + TTL de 7 dias + volume de demonstração é trivial |

---

## Open Questions

| Question | Owner | Due Date | Resolution |
|----------|-------|----------|------------|
| Nome do projeto, integrantes, professor e data para o Slide 1 | Time | Antes da apresentação | **Pendente** — placeholders `{{...}}` nos slides |
| Domínio próprio + certificado ACM para WSS, ou IP do ALB em HTTP? | Time | Antes do deploy | **Resolvido**: HTTP no ALB para a demo (sem custo de domínio); caminho para HTTPS documentado no SDD |
| Região AWS | Time | Antes do deploy | **Resolvido**: `us-east-1` (maior cobertura de Free Tier) |

---

## Rastreabilidade: requisito → critério de avaliação

| Critério da disciplina | Requisitos que o atendem |
|---|---|
| **EC1 — Documentação (SDD)** | Todos os ADRs, `docs/SDD.md`, diagramas de arquitetura, esta especificação |
| **EC2 — Implementação de conceitos** (comunicação indireta, concorrência, escalabilidade) | FR-4 (comunicação em grupo via Pub/Sub), FR-5 (ordenação total), FR-6 (relógios lógicos), FR-13 (concorrência/rate limit), NFR de Escalabilidade |
| **EC3 — Demonstração prática + simulação de falha** | FR-8 (replay sem perda), FR-10 (painel de nós), NFR de Confiabilidade, roteiro de demo + `scripts/kill_node.sh` |
