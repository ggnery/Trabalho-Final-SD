---
intent: 001-chat-tempo-real-salas
artifact: construction-log
created: 2026-08-01T16:40:00Z
updated: 2026-08-01T14:50:00Z
status: complete
---

# Construction Log — SalaViva

Sete bolts executados. Este registro guarda o que a construção **ensinou** — em
especial os três defeitos que só apareceram ao medir e que mudaram o desenho.

---

## Bolt 001 — `001-core-domain`

Relógios de Lamport e vetorial, envelope, fila de hold-back e as portas.

Decisão de implementação registrada em código: os métodos mutadores dos relógios
**não** usam lock. Não é descuido — todo o servidor roda em um event loop
`asyncio` e nenhum desses métodos contém `await`, então o bloco é atômico em
relação às demais corrotinas. Introduzir um `asyncio.Lock` adicionaria pontos de
suspensão a uma seção hoje atômica, criando justamente o entrelaçamento que o
lock pretenderia evitar.

Cobertura resultante: `clocks.py` 97 %, `ordering.py` 100 %, `ports.py` 100 %.

---

## Bolt 002 — `002-messaging-infra`

Adaptadores Redis (Pub/Sub, sequenciador, presença, registro de nós) e DynamoDB,
mais as implementações em memória.

As versões em memória não são um detalhe de teste: elas permitem subir **N nós
do SalaViva no mesmo processo Python**, com relógios independentes, e verificar
ordem total e ausência de perda sem Redis, sem AWS e sem Docker. É o que torna a
prova do requisito central executável na máquina de qualquer avaliador.

---

## Bolt 003 — `003-websocket-gateway`

FastAPI + WebSocket, protocolo Pydantic, JWT, rate limit, health checks.

**Defeito encontrado e corrigido durante o bolt:** a lógica de idempotência
usava string vazia como marcador de reivindicação. Como `""` é *falsy* em Python,
um reenvio seria tratado como envio novo e devolveria `seq = 0`. Substituído por
um marcador explícito (`"pending"`) e teste de `is not None`, com um método
`record` adicionado à porta para sobrescrever o marcador pelo resultado real.

---

## Bolt 004 — `004-clients`

Cliente web e cliente CLI.

O elemento assinatura da UI é o **trilho de sequência**: uma linha vertical
contínua à esquerda das mensagens, com o `seq` impresso e um ponto na cor do nó
de origem. A linha só permanece contínua enquanto a sequência for contígua. O
efeito é que distribuição (várias cores) e ordem total (uma linha ininterrupta)
aparecem na mesma imagem — a tese do projeto vira recurso gráfico.

Decisão tipográfica com significado: monoespaçada para o que a **máquina** afirma
(`seq`, Lamport, `node_id`) e serifada para o que **humanos** disseram.

---

## Bolt 005 — `005-infrastructure`

Docker, Compose de 3 nós, Terraform, scripts de operação.

**Defeito encontrado ao medir — e o mais consequente para a apresentação:** o
nginx local usava `ip_hash` como "equivalente do stickiness do ALB". Todo tráfego
vindo do host chega ao nginx com o mesmo IP de origem (o gateway da rede bridge
do Docker), então o algoritmo concentrou **273 de 273 conexões em um único nó**,
com p95 de 25 s enquanto dois nós ficavam ociosos.

O impacto na demonstração seria pior que o de desempenho: três abas do navegador
na mesma máquina exibiriam o mesmo `node_id`, apagando a evidência visual de
distribuição — o ponto inteiro da demo.

Trocado por `least_conn`. Isso obrigou a **revisar o ADR-007**: investigando o
motivo, ficou claro que o sistema não depende de afinidade para correção (a
conexão não migra, o JWT é stateless, e o `last_seq` recupera o backlog em
qualquer nó). O ADR passou de "sticky sessions são necessárias" para "o sistema
não depende de afinidade; stickiness no ALB é estabilizador, não requisito".

Terraform validado com `terraform fmt -check` e `terraform validate` (Terraform
1.15.8): configuração válida e formatada.

---

## Bolt 006 — `006-quality`

79 testes: 74 sem infraestrutura + 5 end-to-end contra o cluster real.

**Descoberta do e2e:** com Redis real, as mensagens **chegam fora de ordem**. Dois
nós fazem `INCR` e depois `PUBLISH`, e nada obriga a ordem dos publishes a
coincidir com a dos `INCR` — o nó que obteve `seq = 3` pode publicar antes do que
obteve `seq = 2`. Todos os clientes veem a mesma ordem de *chegada*, mas ela não
é a ordem correta.

Isso não é defeito: é a razão de a fila de hold-back existir. O teste foi
corrigido para verificar as duas propriedades separadamente — entrega uniforme
entre clientes, e ordem total **após** a reordenação no cliente. Sob carga, 126
mensagens chegaram fora de ordem em uma única execução, todas corrigidas.

**Terceiro defeito, encontrado no teste de carga:** com 1.200 clientes entrando
em rampa, 8 salas de 120 acusaram lacunas permanentes — sempre em clientes que
entraram por último, sempre em `seq` publicados no instante do `join`.

A causa é a janela do ADR-008 vista de outro ângulo: a mensagem foi publicada
antes de o cliente assinar o tópico (não chega ao vivo) e ainda não foi gravada
(não aparece no backlog). Corrigido com releitura limitada do backlog até cobrir
a faixa conhecida `(last_seq, current_seq]` — registrado como **ADR-009**.

Resultado: de 112/120 para **120/120 salas íntegras**, com p95 caindo de 242 ms
para 18 ms.

Também foi corrigido um defeito no próprio gerador de carga: ele não implementava
o `resync` que o cliente real faz, então reportava como perda do sistema aquilo
que o cliente de verdade recupera do histórico durável.

---

## Bolt 007 — `007-deliverables`

SDD (2.831 linhas, 15 seções), 10 slides no template da disciplina, roteiro
cronometrado de demonstração com perguntas de arguição preparadas.

---

## Resultado consolidado

| Verificação | Resultado |
|---|---|
| Testes | **79 passando** (74 locais + 5 e2e no cluster real) |
| Lint / formatação | `ruff check` e `ruff format --check` limpos |
| Cobertura em `domain/` | 97–100 % |
| Terraform | `validate` = Success, `fmt` limpo |
| Conexões simultâneas medidas | **1.200**, distribuídas 401/400/399 |
| Latência p50 / p95 / p99 | **6,7 / 18,2 / 65,6 ms** |
| Ordem total sob carga | **120/120 salas, 0 divergências** |
| Perda ao derrubar um nó | **0 mensagens** (verificado em e2e) |

## Operações — deploy real na AWS Academy Sandbox

Executado de fato, e foi onde apareceram os defeitos mais caros. Nenhum deles
teria surgido sem rodar no ambiente real.

**Quatro bloqueios da sandbox**, que motivaram a variante `infra/terraform-sandbox/`:
ElastiCache e ECR não liberados, IAM read-only, e KMS só com listagem. Já
previstos ao ler o tutorial do ambiente.

**Três que só apareceram aplicando:**

1. `LabInstanceProfile` não existe na sandbox Cloud Architecting — é do Learner Lab.
2. A sandbox **nega `iam:PassRole`**: nenhuma profile pode ser anexada ao Auto
   Scaling, qualquer que seja. Sem credencial não há DynamoDB, e sem DynamoDB não
   há replay — que é a prova central do critério EC3. Resolvido com DynamoDB
   Local em contêiner ao lado do Redis (ADR registrado em `variables.tf`).
3. O IP privado fixo do Redis colidia na recriação, porque o
   `create_before_destroy` do ASG **se propaga** para tudo de que ele depende.

**Dois defeitos nossos, que teriam falhado ao vivo:**

4. O `kill_node.sh` reportou "cluster caiu para 0 nós" e "restabelecida em
   t+11s". Ambos falsos: uma requisição HTTP perdida era lida como "zero nós", e
   o quórum era declarado olhando só a contagem — que nunca caía, porque o
   heartbeat do nó morto leva 15 s para expirar. Corrigido; as medições reais
   passaram a ser t+13s / t+211s / t+211s.

5. **O stickiness do ALB apagava a evidência de distribuição.** Duas abas do
   mesmo navegador caíam sempre no mesmo nó, porque compartilham o cookie
   `AWSALB`. É o mesmo defeito do `ip_hash` no nginx, por outro caminho — e a
   correção anterior tratou só o balanceador local. Desligado nas duas
   infraestruturas. A lição foi registrada no ADR-007: *"não depender de
   afinidade" só vale se nenhuma camada a impuser*.

**Resultado da validação na nuvem** (ver `docs/SDD.md` §10.5): 3 nós EC2,
30 mensagens com ordem idêntica entre clientes, **duas instâncias derrubadas ao
vivo**, zero mensagens perdidas, e o cliente conectado à instância substituta
recuperando as 30 mensagens do backlog — de uma instância que nunca as viu passar.

## O que ficou por fazer

- Deploy real na AWS não foi executado (exige credenciais do time). O Terraform
  valida e o `README` de `infra/terraform` traz o passo a passo.
- Placeholders `{{INTEGRANTES}}`, `{{PROFESSOR}}`, `{{DATA}}` e `{{INSTITUICAO}}`
  seguem por preencher em `slides/apresentacao.{md,html}` e `docs/SDD.md`.
- A meta de 1.500 conexões no cluster não foi alcançada por limite da máquina de
  teste (3 contêineres + Redis + nginx + gerador de carga no mesmo laptop), não
  por limite da arquitetura. Declarado como parcial em `docs/SDD.md` §3.2.
