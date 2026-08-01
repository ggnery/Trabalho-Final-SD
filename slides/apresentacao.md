# SalaViva — Apresentação Final

Roteiro de 15 minutos, no formato exigido pelo
`orientacoes/Modelo_Apresentacao_Projeto_Disciplina.pdf` (10 slides).
A versão apresentável está em `apresentacao.html`.

Convenções deste arquivo:

- `---` separa um slide do próximo.
- O bloco `> Notas do apresentador` fica **abaixo** de cada slide: é o que se
  fala, não o que se projeta.
- Placeholders em chaves duplas (`{{...}}`) precisam ser preenchidos antes da
  apresentação — eles aparecem também no HTML.

Orçamento de tempo (soma = 15:00):
`0:40 · 1:00 · 0:50 · 1:10 · 1:10 · 1:50 · 1:40 · 2:30 · [demo 2:00] · 0:50 · 1:20`

---

## Slide 1 — Identificação

# SalaViva

**Chat distribuído em tempo real com salas.
Todos os participantes veem a mesma ordem.**

- Integrantes:
  - Gabriel Nery da Silva Espindola — 202200509
  - Giordana de Farias Franco Bueno Bucci — 202200513
  - Gustavo Henrique Valadares — 202205539
  - Carlos Alberto Rodrigues da Silva Junior — 202200498
  - Luiz Felipe Belisário Macedo — 202200538
- Disciplina: Sistemas Distribuídos
- Professor: {{PROFESSOR}}
- Data: {{DATA}}

> ### Notas do apresentador — 0:40
>
> Abrir pelo nome e pela promessa, não pela tecnologia:
> *"SalaViva é um chat em tempo real com salas, rodando distribuído na AWS. A
> promessa do projeto cabe em uma frase: não importa em qual servidor você caiu,
> você vê a conversa exatamente na mesma ordem que todo mundo."*
>
> Apresentar os integrantes em uma frase só. **Não** entrar em tecnologia aqui.
>
> Deixar o gancho para o slide 2: *"Isso parece óbvio até você colocar o segundo
> servidor no ar."*
>
> Se o deck estiver projetado, este é o momento de conferir se o texto está
> legível do fundo da sala. Tecla `F` entra em tela cheia.

---

## Slide 2 — Introdução / Contextualização

**Problema.** Com mais de um servidor, cada usuário pode ver a conversa em uma
ordem diferente.

- **Contexto:** chat escala horizontalmente; a conversa não escala junto
- **Público afetado:** suporte ao cliente, comunidades, salas de aula, jogos
- **Relevância:** a ordem *é* o significado — resposta antes da pergunta muda o
  sentido da conversa
- **Causa raiz:** relógio físico de cada máquina diverge, mesmo com NTP
- **Não é bug de rede:** é propriedade de um sistema distribuído sem ordenação

*Diagrama: a mesma sala, dois usuários, duas ordens.*

> ### Notas do apresentador — 1:00
>
> Contar a história pelo diagrama, apontando para a tela:
> *"Ana está conectada ao nó A, Bruno ao nó B. Ana pergunta 'alguém consegue
> abrir o sistema?'. Bruno responde 'consigo sim'. Se cada nó entregar na ordem
> em que a mensagem chegou nele, Bruno pode ver a própria resposta antes da
> pergunta. A conversa continua legível para uma pessoa, mas o log fica errado —
> e em suporte técnico o log é a prova."*
>
> Reforçar a causa raiz: *"a tentação é ordenar por horário. Não funciona:
> relógios de instâncias EC2 divergem em dezenas de milissegundos mesmo sob NTP.
> Duas mensagens separadas por 5 ms podem trocar de lugar."*
>
> Fechar posicionando o problema como clássico, não exótico: *"esse é
> literalmente o problema que motivou o artigo do Lamport em 1978 — voltamos
> nele no slide de fundamentação."*

---

## Slide 3 — Objetivos

**Geral.** Construir e demonstrar, rodando na AWS, um chat multi-sala que
garante ordem total de mensagens e sobrevive à queda de um nó.

Específicos:

1. Comunicação indireta: nenhum nó conhece outro nó (Pub/Sub)
2. Ordenação: Lamport, relógio vetorial e sequenciador — papéis distintos
3. Escalabilidade: adicionar nó sem reconfigurar os existentes
4. Tolerância a falhas: derrubar uma EC2 ao vivo, zero mensagem perdida
5. Documentação defensável: 8 ADRs justificando cada escolha

> ### Notas do apresentador — 0:50
>
> Ler o objetivo geral e passar rápido pelos específicos — eles reaparecem
> detalhados adiante. O que importa aqui é dizer **por que estão nessa ordem**:
>
> *"Os cinco específicos não são uma lista de tarefas, são os cinco critérios
> pelos quais este trabalho é avaliado. Comunicação indireta, concorrência e
> escalabilidade compõem a nota de implementação de conceitos; a demonstração de
> falha é o critério prático; os ADRs são a nota de documentação."*
>
> Se o professor estiver com a rubrica na mão, ele reconhece o mapeamento —
> isso já vale pontos de organização.

---

## Slide 4 — Nicho de Mercado

- **Público-alvo:** produtos que precisam de chat *dentro* deles — suporte,
  comunidades, salas de aula online, jogos
- **Clientes:** quem não pode terceirizar a conversa (dado sensível, auditoria,
  ordem verificável)
- **Mercado:** chat virou infraestrutura — Discord, Slack, Matrix, Sendbird
- **Diferencial:** ordem total *verificável*, arquitetura auditável (8 ADRs),
  autohospedável, ~US$ 0,07/h em Free Tier

| | Ordem total garantida | Autohospedável | Arquitetura auditável |
|---|---|---|---|
| Discord / Slack | não documentada | não | fechada |
| Matrix | ordem por DAG, eventual | sim | aberta |
| **SalaViva** | **sim, por sala** | **sim** | **8 ADRs** |

> ### Notas do apresentador — 1:10
>
> Sério, não promocional. O ângulo honesto é o de nicho:
>
> *"Não estamos competindo com o Discord em recursos — eles têm voz, vídeo,
> milhões de usuários. Competimos em uma propriedade específica: garantia de
> ordem que você pode verificar, em infraestrutura que você controla."*
>
> Sobre o Matrix, que é o concorrente técnico real: *"o Matrix é federado e
> aberto, mas resolve ordenação com um grafo de eventos e reconciliação
> eventual — dois servidores podem exibir ordens diferentes por um intervalo.
> Nós escolhemos um sequenciador por sala: mais restritivo, e por isso
> determinístico."*
>
> Fechar no caso concreto: *"o cliente típico é um SaaS de suporte que precisa
> exportar a transcrição de um atendimento como prova. Se a ordem do log não é
> garantida, a transcrição não vale nada."*

---

## Slide 5 — Requisitos Atualizado

| | |
|---|---|
| **Problema** | Ordem divergente entre nós; nó que cai leva mensagens junto |
| **Solução** | Pub/Sub + sequenciador atômico + replay por histórico |
| **Público** | Salas de 2 a centenas de participantes, multi-sala por conexão |
| **Recursos** | 3× EC2 `t3.micro` (ASG 2–4), ALB, ElastiCache Redis, DynamoDB, ECR, CloudWatch |
| **Custos** | ≈ **US$ 0,07/h** com o ambiente de pé · ≈ US$ 0,30 numa sessão de 4 h · sem NAT Gateway (economia de ~US$ 32/mês por AZ) |
| **Benefícios** | Mesma ordem para todos · zero perda na queda de um nó · escala por sala |

Metas mensuráveis: p95 < 200 ms · ≥ 1.000 conexões simultâneas · reconexão < 5 s
· ASG restaura a capacidade em < 3 min

> ### Notas do apresentador — 1:10
>
> Este slide é o "estado atual dos requisitos" — o que mudou desde a proposta
> inicial. Dois pontos merecem ser ditos em voz alta:
>
> **Custo.** *"O Free Tier cobre 750 horas de EC2 por mês, o equivalente a uma
> instância ligada o tempo todo. Com três nós, o consumo é de cerca de 2.160
> horas — o excedente é cobrado. Por isso o ambiente sobe para a apresentação e
> desce depois: `terraform destroy` está documentado no README da infra."*
>
> **A ausência do NAT Gateway.** *"A boa prática seria EC2 em subrede privada
> com NAT. O NAT custa cerca de 32 dólares por mês por zona e não é Free Tier —
> seria o maior item da conta, sozinho maior que todo o resto. Optamos por
> subrede pública com Security Groups encadeados: a porta 8000 só aceita
> tráfego do Security Group do balanceador, então a aplicação não é alcançável
> da internet mesmo com IP público. Isso está registrado como ADR-006, com a
> configuração de produção descrita."*
>
> Assumir o desvio explicitamente vale mais do que escondê-lo — e antecipa a
> pergunta.

---

## Slide 6 — Fundamentação Científica

| Referência | O que fundamenta no SalaViva |
|---|---|
| **Lamport (1978)**, *Time, Clocks and the Ordering of Events* | Relógio lógico e a relação *happened-before*; por que timestamp físico não ordena |
| **Mattern (1988)** / Fidge (1988) | Relógio vetorial: detecta concorrência, o que o relógio escalar não faz |
| **Coulouris, cap. 6** — Comunicação indireta | Pub/Sub: desacoplamento no espaço e no tempo entre emissor e receptor |
| **Coulouris, cap. 14** — Tempo e estados globais | Ordenação de eventos e a impossibilidade de ordem global por relógio físico |
| **Tanenbaum & Van Steen** — Multicast ordenado | Sequenciador centralizado como forma de ordem total |
| **RFC 6455** — The WebSocket Protocol | Canal full-duplex persistente; handshake de *upgrade* que o ALB precisa suportar |
| **Brewer (2000); Gilbert & Lynch (2002)** — CAP | CP para ordenação, AP para presença: o trade-off é por dado, não pelo sistema |

> ### Notas do apresentador — 1:50
>
> **Este slide vale 15% da nota.** Não ler a tabela: percorrer as três ideias
> que estruturam o projeto.
>
> 1. *"Lamport, 1978, é o artigo fundador. Ele mostra que em um sistema
>    distribuído não existe 'o mesmo instante' — só existe a relação causal
>    'aconteceu antes'. Implementamos o relógio de Lamport exatamente como no
>    artigo: incrementa a cada evento local, e no recebimento faz o máximo entre
>    o próprio relógio e o da mensagem, mais um."*
>
> 2. *"Só que Lamport tem uma limitação conhecida: se o relógio de A é menor que
>    o de B, isso **não** significa que A causou B — os dois podem ser
>    concorrentes. Quem fecha essa lacuna é o relógio vetorial, de Mattern e
>    Fidge, em 1988. Implementamos os dois, com papéis separados, e o nosso
>    cliente de terminal marca visualmente as mensagens concorrentes."*
>
> 3. *"E nenhum dos dois define a ordem de exibição. Lamport dá ordem parcial; a
>    interface precisa de uma lista linear, ordem total. Tanenbaum descreve a
>    solução que adotamos: um sequenciador. O CAP, de Brewer e formalizado por
>    Gilbert e Lynch, é o que nos permite escolher lado por dado — ordenação é
>    CP, presença é AP. Ver um usuário fantasma por 15 segundos é irrelevante;
>    ver uma mensagem fora de ordem, não."*
>
> Se sobrar tempo, mencionar Coulouris cap. 6 como a definição formal de
> comunicação indireta que a arquitetura implementa ao pé da letra.

---

## Slide 7 — Solução

- **Onde se aplica:** qualquer produto com chat multi-sala; demonstrado na AWS
  `us-east-1`
- **Técnica:** Redis Pub/Sub (fan-out) + `INCR` atômico por sala (ordem total) +
  fila de *hold-back* no cliente + DynamoDB `(room_id, seq)` para replay
- **Benefícios:** ordem idêntica para todos · nó pode morrer sem perder mensagem
  · salas escalam independentes

**Justificativa — o que foi descartado e por quê:**

| Alternativa | Motivo do descarte |
|---|---|
| Ordenar por timestamp físico | Relógios de EC2 divergem sob NTP → inversão de mensagens |
| Consenso (Raft / Paxos) | Superdimensionado: existe um árbitro natural, e o custo de quórum não se paga |
| SNS + SQS no lugar do Redis | SQS entrega a **um** consumidor (balanceamento, não difusão); 100–500 ms de latência |

> ### Notas do apresentador — 1:40
>
> A técnica em uma frase: *"toda mensagem, antes de ser difundida, recebe um
> número de sequência de um `INCR` atômico no Redis. `INCR` é atômico e o Redis
> é single-threaded, então ele serializa por construção — sem lock, sem retry.
> Esse número é a ordem total da sala. O cliente só exibe em ordem contígua de
> `seq`, através de uma fila de hold-back."*
>
> Depois vender a tabela de descartes, que é onde está o mérito de engenharia:
>
> - *"Timestamp está descartado por construção — é o problema do slide 2."*
> - *"Raft resolveria, mas resolve um problema maior do que o nosso: múltiplos
>   proponentes sob partição. Aqui existe um árbitro natural, que é o Redis.
>   Pagar latência de quórum por isso seria errado."*
> - *"SNS e SQS são os serviços gerenciados óbvios da AWS, e não servem: SQS
>   entrega cada mensagem a um consumidor só — é balanceamento de carga, não
>   difusão. Para fazer fan-out precisaríamos de uma fila por nó, criada e
>   destruída conforme o Auto Scaling — o que reintroduz exatamente o
>   acoplamento nó-a-nó que a comunicação indireta existe para eliminar."*
>
> Se perguntarem "e a durabilidade do Redis Pub/Sub?": *"é at-most-once, e
> assumimos isso. A durabilidade vem da camada de persistência, não do canal —
> está no slide 10, nas limitações."*

---

## Slide 8 — Arquitetura da Solução

*Diagrama: Cliente → ALB → 3× EC2 (ASG) → ElastiCache Redis → DynamoDB.*

Fluxo de uma mensagem:

1. Cliente envia por WebSocket ao nó A
2. Nó A valida JWT, aplica `SET NX` de idempotência e `lamport.tick()`
3. `INCR chat:seq:{sala}` → **ordem total**
4. `PUBLISH chat:room:{sala}` → fan-out; gravação no DynamoDB em paralelo
5. **Todos** os nós recebem — inclusive o emissor (caminho único de entrega)
6. Cliente reordena por `seq` na fila de hold-back e exibe

**Componentes:** ALB (health check em `/readyz`, 15 s) · ASG (min 2, desejado 3,
max 4) · Redis (Pub/Sub, `INCR`, ZSET de presença e de nós) · DynamoDB
(`room_id`, `seq`)

> ### Notas do apresentador — 2:30 (+ 2:00 de demonstração ao vivo)
>
> **O slide central.** Percorrer o diagrama da esquerda para a direita.
>
> A propriedade a destacar primeiro: *"repare que não existe nenhuma seta entre
> os nós. Nenhum nó conhece nenhum outro nó — um nó só conhece o Redis. É isso
> que faz o Auto Scaling funcionar sem orquestração: subir o quarto nó não exige
> reconfigurar os outros três."*
>
> Depois o passo 5, que é o mais contraintuitivo: *"o nó que publicou **não**
> entrega a mensagem localmente por atalho. Ele espera ela voltar pelo Pub/Sub,
> como qualquer outro nó. Custa um round-trip de menos de um milissegundo, e em
> troca existe um único caminho de entrega no sistema. Se houvesse o atalho, os
> clientes do nó emissor veriam a ordem de processamento local e os dos outros
> nós veriam a ordem do `seq` — duas ordens possíveis, e a garantia central do
> projeto viraria condicional."*
>
> Encerrar com a divisão de responsabilidades entre os dois bancos: *"Redis
> guarda o que precisa de latência e não de durabilidade — presença, contador.
> DynamoDB guarda o que precisa de durabilidade e não de latência — histórico. A
> chave composta `(room_id, seq)` faz o replay de reconexão ser uma única
> `Query` já ordenada pelo índice."*
>
> ### Demonstração ao vivo (2:00) — critério EC3
>
> Sequência ensaiada, sem improviso:
>
> 1. Projetar `/dashboard` (3 nós vivos) e duas abas de chat mostrando
>    `node_id` diferentes. Enviar mensagens: `seq` avança nas duas.
> 2. `make demo-kill` (ou `scripts/kill_node.sh --aws`) — derrubar a instância
>    que aparece na aba da esquerda.
> 3. Narrar enquanto acontece: nó some do painel em ≤ 15 s (sweeper); ALB
>    remove do pool em ≤ 45 s (3 falhas × 15 s em `/readyz`); cliente reconecta
>    em < 5 s exibindo **outro** `node_id`.
> 4. **O ponto da demonstração:** continuar enviando mensagens durante a queda e
>    mostrar que o `seq` não regrediu nem pulou. *"O contador vive no Redis e o
>    histórico no DynamoDB — nenhum dos dois morreu com a instância. O cliente
>    reconectou informando o último `seq` que tinha e recebeu exatamente o que
>    perdeu."*
> 5. Mostrar o ASG recriando a instância (< 3 min). Se o tempo apertar, deixar
>    rodando em segundo plano e voltar a ele no slide 10.
>
> **Plano B:** se a rede da sala falhar, rodar a mesma demonstração no cluster
> local (`make up` + `make demo-kill`) — o comportamento é idêntico. Vídeo de
> backup gravado como último recurso.

---

## Slide 9 — Tecnologias

- **Linguagens:** Python 3.13 (backend, CLI, carga) · JavaScript ES2022 sem
  build · HCL (Terraform) · Bash
- **Frameworks:** FastAPI + Uvicorn (`uvloop`) · Pydantic v2 (contrato validado
  na borda)
- **Bancos:** ElastiCache Redis 7.1 (coordenação) · DynamoDB on-demand
  (histórico, TTL 7 dias)
- **Bibliotecas:** `redis.asyncio` · `aioboto3` · `PyJWT` · `structlog` ·
  `websockets` · `pytest` · `ruff` · `matplotlib`
- **Infra e operação:** Docker + Compose · ECR · EC2 em ASG · ALB · Terraform ·
  CloudWatch Logs · `uv`

Uma escolha explicada: **um worker por nó**. Cada nó tem estado em memória
(conexões e relógio lógico); múltiplos workers criariam relógios divergentes
dentro da mesma instância.

> ### Notas do apresentador — 0:50
>
> Slide rápido — não ler a lista inteira, ela está projetada. Dizer só o que não
> é óbvio:
>
> - *"`asyncio` dá concorrência cooperativa: milhares de conexões WebSocket em
>   um processo, sem uma thread por conexão. É o modelo que a disciplina discute
>   em processos e threads."*
> - *"Cliente web em JavaScript puro, sem etapa de build: o avaliador não
>   precisa rodar `npm install`, o próprio backend serve o arquivo."*
> - *"Um worker por nó porque o relógio de Lamport é, por definição, estado de
>   um processo. Dois workers na mesma máquina seriam dois processos com
>   relógios independentes e sem canal entre eles — descaracterizaria o
>   algoritmo."*
>
> Se perguntarem por que Python e não Go: foi decisão do time por legibilidade;
> o gargalo medido não foi a linguagem.

---

## Slide 10 — Conclusão

**Verificado** (cluster de 3 nós):

- Ordem total: **OK** em todas as salas · 0 divergências entre clientes
- **1.200** conexões WebSocket simultâneas · 0 falhas de conexão
- p95 de **37 ms** (meta < 200 ms) · p99 109 ms · ~1.500 msg entregues/s
- Queda de um nó: **0 mensagens perdidas**

**Limitações assumidas:**

- ElastiCache single-node é **ponto único de falha** (Multi-AZ ficou fora por
  custo)
- Redis Pub/Sub é **at-most-once** — mitigado por replay no DynamoDB, não
  resolvido no canal
- O sequenciador **serializa por sala** — teto de escrita de uma sala é o teto
  do `INCR`
- Relógio vetorial cresce O(n) no número de nós dentro do envelope
- EC2 em subrede pública (sem NAT) é desvio consciente de boa prática

**Próximos passos:** ElastiCache Multi-AZ com failover · Redis Streams
(at-least-once) · particionar o sequenciador · WSS com domínio e ACM

> ### Notas do apresentador — 1:20
>
> Abrir pelos números medidos, com a ressalva de origem: *"esses números são do
> cluster de três nós; a execução na AWS está no repositório com a mesma
> ferramenta de carga."* Se a medição na AWS tiver sido feita antes da
> apresentação, **atualizar o slide** e citar a fonte correta.
>
> Ser rápido nos benefícios e **demorar nas limitações** — é o que separa um
> trabalho apresentado de um trabalho compreendido:
>
> *"A limitação mais séria é o ElastiCache single-node. Se o Redis cair, nenhuma
> mensagem nova é ordenada e o chat para. Isso é deliberado: preferimos falha
> coerente a degradação silenciosa com ordem errada. A mitigação de produção é
> Multi-AZ com réplica e failover automático — ficou fora porque não é Free
> Tier, não porque não sabíamos que era necessário."*
>
> *"A segunda é que o Pub/Sub do Redis é at-most-once: uma mensagem publicada
> enquanto um nó está desconectado é perdida por aquele nó. O trade-off é
> consciente — canal rápido e não durável, mais armazenamento durável, em vez de
> um canal durável e lento. O cliente detecta a lacuna e pede `resync`; ele nunca
> silencia uma perda."*
>
> Fechar voltando à promessa do slide 1: *"a frase do começo era: não importa em
> qual servidor você caiu, você vê a mesma ordem. Foi isso que derrubamos uma
> instância ao vivo para mostrar."*
>
> Encerrar e abrir para perguntas. Perguntas prováveis estão no `README.md`
> deste diretório.
