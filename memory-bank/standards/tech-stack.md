---
standard: tech-stack
status: approved
created: 2026-08-01T16:20:00Z
updated: 2026-08-01T16:20:00Z
---

# Tech Stack

Core technology decisions for this project.

---

## Languages

**Decision**: Python 3.13 (backend, CLI, testes, carga) + JavaScript ES2022 sem build step (cliente web).

**Rationale**:
- Python foi requisito explícito do time ("pela simplicidade").
- `asyncio` dá concorrência cooperativa de alta densidade: milhares de conexões WebSocket por processo sem uma thread por conexão — exatamente o padrão que a disciplina cobra em "Processos, Threads e Virtualização".
- Cliente web em JS puro (sem React/bundler) elimina toolchain de frontend: o arquivo é servido estaticamente pelo próprio backend, então a demo tem zero passos de build.

**Alternatives considered**:
- Node.js/TypeScript — ecossistema WebSocket mais maduro, mas fora do requisito do time.
- Go — melhor throughput bruto, mas curva de aprendizado maior e menos legível na arguição.

---

## Framework

**Decision**: FastAPI 0.115+ sobre Uvicorn (`uvloop` + `httptools`).

**Rationale**:
- Suporte nativo a WebSocket via Starlette, com o mesmo processo servindo HTTP (health checks, métricas, UI estática) — um único artefato para o ALB.
- Pydantic v2 valida o protocolo de mensagens na borda, transformando erro de contrato em `4xx` explícito em vez de exceção em runtime.
- `uvloop` eleva o teto de conexões concorrentes por instância `t3.micro`.

**Alternatives considered**:
- `websockets` puro — mais leve, mas exigiria servidor HTTP separado para health check do ALB.
- Django Channels — precisa de camada ASGI + Redis backend próprio; peso desnecessário.

---

## Infrastructure & Deployment

**Decision**: AWS — EC2 em Auto Scaling Group atrás de Application Load Balancer, ElastiCache for Redis como broker Pub/Sub, DynamoDB para histórico. Provisionado por Terraform. Conta AWS própria (Free Tier).

| Componente | Serviço AWS | Justificativa |
|---|---|---|
| Borda / balanceamento | Application Load Balancer | Único ELB da AWS com suporte a upgrade WebSocket (HTTP/1.1 `Upgrade`); faz health check ativo e remove nó morto do pool. |
| Nós de aplicação | EC2 `t3.micro` em ASG (min 2, max 4) | ASG recria a instância derrubada automaticamente — **é o que torna a demonstração de falha do critério EC3 possível**. Serverless não teria "instância para derrubar". |
| Comunicação indireta | ElastiCache for Redis 7 (Pub/Sub) | Desacopla os nós: quem publica não conhece quem consome. Latência sub-milissegundo, adequada a chat em tempo real. |
| Sequenciador total | ElastiCache Redis (`INCR` atômico) | Ordem total por sala com uma única operação atômica, sem coordenação distribuída custosa. |
| Histórico / replay | DynamoDB (on-demand) | Chave composta `(room_id, seq)` dá replay ordenado nativo. On-demand evita provisionar capacidade. |
| Registro de imagem | Amazon ECR | `docker pull` no `user_data` sem credenciais embutidas (via IAM instance profile). |
| Observabilidade | CloudWatch Logs + endpoint `/metrics` | Logs centralizados sobrevivem à morte da instância — essencial para provar a falha na demo. |

**Rationale da escolha EC2 sobre Serverless**: a proposta da disciplina sugere API Gateway WebSocket + Lambda, mas o critério de avaliação EC3 diz textualmente *"o professor pode solicitar a simulação de uma falha (ex: derrubar uma instância EC2)"*. Arquitetura serverless não expõe instância para derrubar; a demonstração de tolerância a falhas ficaria abstrata. Além disso, Lambda + ElastiCache exige VPC attachment, o que reintroduz cold start de ~1s — inaceitável para o handshake de um chat. A decisão está registrada em [ADR-001](decision-index.md).

---

## Package Manager

**Decision**: `uv` (Astral) com `pyproject.toml` + `uv.lock`.

**Rationale**: resolução de dependências 10-100× mais rápida que pip, lockfile determinístico (o build da imagem Docker é reproduzível), e um único binário — sem `virtualenv`/`pip-tools`/`poetry` empilhados. Fallback documentado para `pip install -r requirements.txt` caso a máquina do avaliador não tenha `uv`.

---

## Authentication

**Decision**: JWT HS256 de curta duração, emitido por `POST /auth/login`, validado no handshake do WebSocket.

**Rationale**: a disciplina dedica um seminário inteiro a "Segurança em Sistemas Distribuídos" e o item pede modelagem de controle de acesso para APIs distribuídas. JWT assinado é **stateless** — qualquer nó do ASG valida o token sem consultar estado compartilhado, o que é a propriedade que faz autenticação funcionar em cluster sem sessão pegajosa no plano de auth. Sem senha (projeto acadêmico): o login aceita qualquer `username` e emite o token; a validação criptográfica da assinatura é real.

---

## Runtime & Container

| Item | Escolha |
|---|---|
| Base image | `python:3.13-slim` |
| Processo | `uvicorn` single worker por container, N containers = N instâncias EC2 |
| Orquestração local | Docker Compose (3 nós + Redis + nginx) para paridade com a nuvem |

**Rationale do single worker**: cada nó mantém estado em memória (mapa de conexões WebSocket + relógio lógico de Lamport). Múltiplos workers `fork` no mesmo host criariam relógios divergentes sem canal entre eles. Um worker por nó mantém a identidade `node_id` ↔ relógio 1:1, que é exatamente o modelo formal de processo do algoritmo de Lamport.
