# SalaViva — Chat Distribuído em Tempo Real com Salas (Pub/Sub)

Projeto final da disciplina de **Sistemas Distribuídos** — Projeto 3 da proposta.

Aplicativo de mensagens instantâneas com múltiplas salas, distribuído horizontalmente
na AWS, que suporta milhares de conexões WebSocket simultâneas e **garante que todos
os participantes de uma sala vejam as mensagens exatamente na mesma ordem**, mesmo
conectados a instâncias EC2 diferentes.

```
   Cliente Web / CLI
          │ WebSocket
          ▼
   Application Load Balancer  ──health check──▶ /readyz
   ┌──────┴──────┬────────────┐
  EC2          EC2          EC2      ← Auto Scaling Group (min 2, max 4)
   │             │            │        cada nó: relógio de Lamport + vetorial
   └──────┬──────┴────────────┘
          ▼
   ElastiCache Redis
     PUB/SUB  chat:room:{id}     ← comunicação indireta (fan-out)
     INCR     chat:seq:{id}      ← ordem total, atômica
     ZSET     chat:presence:{id} ← presença com heartbeat
          ▼
   DynamoDB  (room_id, seq)      ← histórico + replay na reconexão
```

---

## O que este projeto demonstra

| Conceito da disciplina | Onde está | Como verificar |
|---|---|---|
| **Comunicação em grupo** | Pub/Sub por sala, `infra/redis_bus.py` | `pytest -k atravessa` |
| **Comunicação indireta** | Nenhum nó conhece outro nó; só o Redis | `ports.py` — nenhum método recebe destinatário |
| **Relógio de Lamport** | `domain/clocks.py` | `pytest tests/unit/test_clocks.py` |
| **Relógio vetorial** | `domain/clocks.py` | concorrência marcada no cliente CLI |
| **Ordenação total** | `INCR` atômico + hold-back queue | `pytest -k ordem_total` |
| **Concorrência** | `asyncio`, uma Task por conexão, token bucket | `pytest -k rate_limit` |
| **Escalabilidade** | ASG, nós sem estado compartilhado | `loadtest/` — 1200 conexões medidas |
| **Tolerância a falhas** | `seq` no Redis + histórico no DynamoDB | `scripts/kill_node.sh` |

---

## Início rápido

### Modo autônomo (sem nenhuma dependência)

```bash
uv venv && uv pip install -e ".[dev]"
SALAVIVA_REDIS_URL=memory:// .venv/bin/python -m uvicorn salaviva.main:app --port 8000
```

Abra <http://localhost:8000>. Um nó, sem Redis, sem AWS.

### Cluster local (3 nós — paridade com a nuvem)

```bash
make up                                 # 3 nós + Redis + DynamoDB Local + nginx
open http://localhost:8080              # chat
open http://localhost:8080/dashboard    # painel de nós
```

Abra **duas abas** do chat: o cabeçalho mostra o `node_id` de cada uma, e elas serão
diferentes — as mensagens cruzam o cluster pelo Redis.

### Simulação de falha

```bash
./scripts/watch_cluster.sh    # em um terminal
./scripts/kill_node.sh        # em outro: derruba um nó
```

O nó some do painel em ≤ 15 s, os clientes reconectam em outro nó, e a sequência de
mensagens continua **sem lacuna**:

```bash
curl -s localhost:8080/api/rooms/geral/messages | python3 -c "import sys,json;print(json.load(sys.stdin)['contiguous'])"
# True
```

### Deploy na AWS

Há **duas** infraestruturas, e usar a errada faz o `apply` falhar:

| Sua conta | Pasta | Por quê |
|---|---|---|
| AWS comum | `infra/terraform/` | Arquitetura de referência: ElastiCache, ECR, IAM sob medida. É a documentada no SDD. |
| **AWS Academy Sandbox** | `infra/terraform-sandbox/` | A sandbox não libera ElastiCache, ECR nem criação de IAM. A variante substitui cada um sem mudar o comportamento do sistema. |

```bash
cd infra/terraform          # ou infra/terraform-sandbox
terraform init && terraform apply
```

Cada pasta tem seu próprio `README.md` com o passo a passo. **Rode `terraform
destroy` após a apresentação** — na sandbox isso é crítico, o orçamento é de
US$ 20 sem reposição.

---

## Testes

```bash
make test        # 74 testes, sem infraestrutura — roda em qualquer máquina
make test-e2e    # 5 testes contra o cluster real (exige `make up`)
make loadtest    # carga + verificação de ordem total sob concorrência
```

Resultados medidos no cluster local de 3 nós (1200 conexões, 120 salas):

| Métrica | Medido | Meta |
|---|---|---|
| Conexões simultâneas | **1200 / 1200** | ≥ 1000 |
| Latência fim a fim p50 | **6,7 ms** | — |
| Latência fim a fim p95 | **18,2 ms** | < 200 ms |
| Latência fim a fim p99 | **65,6 ms** | < 500 ms |
| Handshake p95 | **10,1 ms** | < 300 ms |
| Ordem total | **120/120 salas íntegras** | íntegra |
| Chegadas fora de ordem no Pub/Sub | 126 — todas corrigidas pela hold-back queue | — |

A última linha é o achado mais interessante: o Pub/Sub **entrega fora de ordem** na
prática, e a fila de hold-back do cliente é o que restaura a ordem correta. Não é uma
otimização — é o que faz a garantia valer.

> Medições feitas com os 3 nós, o Redis, o nginx **e** o gerador de carga competindo
> pela CPU do mesmo laptop. A latência varia entre execuções por essa razão; o p50 é
> estável em torno de 7 ms. Acima de ~5.000 entregas/s nesse arranjo o conjunto satura
> — o limite é da máquina de teste, não da arquitetura.

### Verificado também na AWS real

O sistema foi implantado e exercitado em três instâncias EC2 sob Auto Scaling,
atrás de um Application Load Balancer. **Duas instâncias foram encerradas ao vivo**
com `aws ec2 terminate-instances`:

| Verificação | Resultado |
|---|---|
| Ordem idêntica entre clientes em instâncias distintas | **sim** — 30 mensagens, contíguas 1–30 |
| Nó sumiu do registro após a queda | **t+13 s** (TTL do heartbeat: ninguém notificou a morte) |
| Auto Scaling repôs a capacidade | **t+211 s** |
| Mensagens perdidas nas duas quedas | **zero** — histórico contíguo em ambas |
| Cliente na instância substituta recuperou o backlog | **30 de 30 mensagens** |
| `seq` após a falha | continuou em **31**, não regrediu |

O detalhe que mais convence: o cliente que recuperou o histórico estava conectado
a uma instância **criada pelo Auto Scaling depois** de aquelas mensagens terem
sido enviadas. Ela nunca as viu passar — leu tudo do armazenamento durável.

Detalhes e a leitura completa em [`docs/SDD.md` §10.5](docs/SDD.md).

---

## Estrutura

```
src/salaviva/
├── domain/       lógica pura: relógios lógicos, envelope, hold-back queue
├── ports.py      interfaces (Protocol) — a fronteira hexagonal
├── infra/        adaptadores Redis e DynamoDB + versões em memória
├── app/          casos de uso: publicar e entregar
├── ws/           borda WebSocket: protocolo, conexões, gerenciador
├── api/          borda HTTP: auth, health, salas
└── static/       cliente web (sem build)

client/cli/       cliente de terminal (mostra seq, Lamport, concorrência)
loadtest/         gerador de carga + verificador de ordem total
infra/terraform/  infraestrutura como código
tests/            unit · integration · e2e
docs/             SDD, protocolo, roteiro da demonstração
slides/           apresentação (10 slides no template da disciplina)
memory-bank/      artefatos do processo AI-DLC (standards, ADRs, requisitos)
```

`domain/` não importa nada de `infra/`. A consequência prática: os algoritmos que a
disciplina avalia são testáveis sem Redis, sem AWS e sem Docker.

---

## Documentação

| Documento | Conteúdo |
|---|---|
| [`docs/SDD.md`](docs/SDD.md) | Software Design Document — arquitetura, justificativas, diagramas |
| [`docs/protocolo.md`](docs/protocolo.md) | Contrato WebSocket cliente ↔ servidor |
| [`docs/roteiro-demo.md`](docs/roteiro-demo.md) | Roteiro cronometrado de 15 min + perguntas da arguição |
| [`memory-bank/standards/decision-index.md`](memory-bank/standards/decision-index.md) | 8 ADRs com trade-offs explícitos |
| [`slides/apresentacao.html`](slides/apresentacao.html) | Apresentação (abrir no navegador) |

---

## Decisões que valem a pergunta

**Por que EC2 e não Lambda, se a proposta sugeria serverless?** Porque o critério de
avaliação pede a simulação de uma falha derrubando uma instância EC2. Sob Lambda não
há instância para derrubar, e a tolerância a falhas viraria uma afirmação sobre a AWS
em vez de uma propriedade demonstrável do sistema. Ver ADR-001.

**Por que Redis Pub/Sub e não SNS/SQS?** SQS entrega a **um** consumidor por fila — é
balanceamento, não difusão. Replicar fan-out exigiria uma fila por nó, criada e
destruída conforme o ASG escala, reintroduzindo o acoplamento nó-a-nó que a
comunicação indireta existe para eliminar. Mais a latência: 100–500 ms contra < 1 ms.
Ver ADR-002.

**Se o relógio de Lamport ordena eventos, por que existe o `seq`?** Lamport dá ordem
*parcial*: `L(a) < L(b)` não implica que `a` causou `b`. A interface precisa de uma
lista linear — ordem *total*. O `seq` faz esse papel; o Lamport estabelece
causalidade; o relógio vetorial detecta concorrência. Três mecanismos, três papéis
distintos. Ver ADR-003 e ADR-005.

---

## Licença

MIT — ver [LICENSE](LICENSE).
