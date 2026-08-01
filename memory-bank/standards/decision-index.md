---
artifact: decision-index
status: active
created: 2026-08-01T16:20:00Z
updated: 2026-08-01T14:50:00Z
---

# Índice de Decisões Arquiteturais (ADRs)

Registro das decisões estruturais do SalaViva. Cada entrada segue o formato Contexto → Decisão → Consequências.

| ID | Decisão | Status | Critério de avaliação impactado |
|---|---|---|---|
| [ADR-001](#adr-001) | EC2 + Auto Scaling em vez de API Gateway WebSocket + Lambda | Aceita | EC1, EC3 |
| [ADR-002](#adr-002) | Redis Pub/Sub como broker, não SNS/SQS | Aceita | EC2 |
| [ADR-003](#adr-003) | Ordem total via `INCR` atômico, não via consenso nem timestamp físico | Aceita | EC2 |
| [ADR-004](#adr-004) | Emissor recebe a própria mensagem pelo Pub/Sub (caminho único de entrega) | Aceita | EC2 |
| [ADR-005](#adr-005) | Lamport **e** relógio vetorial, com papéis distintos | Aceita | EC2 |
| [ADR-006](#adr-006) | EC2 em subrede pública sem NAT Gateway | Aceita | EC1 |
| [ADR-007](#adr-007) | O sistema **não depende** de afinidade de sessão (revisada após medição) | Aceita | EC3 |
| [ADR-008](#adr-008) | Persistência assíncrona fora do caminho crítico de entrega | Aceita | EC2 |
| [ADR-009](#adr-009) | Backlog do `join` relido até ficar contíguo, com tentativas limitadas | Aceita | EC2, EC3 |

---

## ADR-001

### EC2 + Auto Scaling em vez de API Gateway WebSocket + Lambda

**Contexto.** A proposta da disciplina sugere, para o Projeto 3, a tríade API Gateway (WebSocket API) + AWS Lambda + ElastiCache. Simultaneamente, o critério de avaliação EC3 estabelece que *"o professor pode solicitar a simulação de uma falha (ex: derrubar uma instância EC2) para verificar a tolerância a falhas do sistema"*.

**Decisão.** Adotar nós EC2 em Auto Scaling Group atrás de um Application Load Balancer.

**Justificativa.**
1. **A arquitetura serverless não tem instância para derrubar.** Sob Lambda, a demonstração de tolerância a falhas seria uma afirmação sobre a plataforma da AWS, não uma propriedade demonstrável do sistema construído pelo grupo. Com ASG, derruba-se uma instância ao vivo e observa-se: clientes reconectando, `seq` sem lacuna, ASG recriando o nó.
2. **Lambda com ElastiCache exige VPC attachment**, que reintroduz cold start na casa de ~1 s. Para um handshake de WebSocket em chat, isso é percebido como travamento.
3. **Custo de estado.** Lambda é sem estado por construção; o relógio lógico de Lamport é, por definição, estado por processo. Modelar Lamport sobre Lambda exigiria externalizar o relógio para o Redis a cada evento — o que descaracteriza o algoritmo (deixa de ser um relógio *local* de processo) e transforma um `max()` em memória em um round-trip de rede.

**Consequências.**
- (+) Demonstração de falha concreta e verificável, atendendo EC3 integralmente.
- (+) Relógio de Lamport implementado fielmente ao modelo formal de processo.
- (+) Sem cold start; latência estável.
- (−) Instâncias rodando 24/7 consomem Free Tier (mitigado: `t3.micro`, `terraform destroy` documentado).
- (−) Responsabilidade de patching do SO, irrelevante no horizonte do projeto.

---

## ADR-002

### Redis Pub/Sub como broker, não SNS/SQS

**Contexto.** O critério EC2 exige "uso efetivo de comunicação indireta (filas/tópicos)". A AWS oferece SNS (tópicos) e SQS (filas) como serviços gerenciados; o ElastiCache Redis oferece Pub/Sub.

**Decisão.** Usar Redis Pub/Sub como canal de fan-out entre nós.

**Justificativa.**
1. **Latência.** SNS→SQS→polling tem latência típica de 100–500 ms fim a fim. Redis Pub/Sub entrega em < 1 ms na mesma VPC. Chat em tempo real com 300 ms de atraso é percebido como quebrado.
2. **Semântica correta para o caso.** SQS entrega a mensagem a **um** consumidor por fila — é balanceamento de carga, não difusão. Para replicar fan-out com SQS seria preciso uma fila por nó, criada e destruída conforme o ASG escala. Isso reintroduz exatamente o acoplamento nó-a-nó que a comunicação indireta existe para eliminar.
3. **Redis já é necessário** para o sequenciador (`INCR`) e a presença (`ZSET`). Um componente a menos na arquitetura.

**Consequências.**
- (+) Latência compatível com tempo real.
- (+) Fan-out nativo, sem gerência de filas por nó.
- (−) Redis Pub/Sub é *at-most-once*: mensagem publicada enquanto um nó está desconectado é perdida por aquele nó. **Mitigação**: o histórico durável em DynamoDB e o replay por `last_seq` na reconexão cobrem a lacuna — a durabilidade vem da camada de persistência, não do canal. O trade-off é consciente: canal rápido e não-durável + armazenamento durável, em vez de canal durável e lento.
- (−) ElastiCache single-node é ponto único de falha. Documentado como limitação; a mitigação em produção seria Multi-AZ com réplica e failover automático (não habilitado por custo de Free Tier).

---

## ADR-003

### Ordem total via `INCR` atômico, não via consenso nem timestamp físico

**Contexto.** O requisito do Projeto 3 é "garantir a ordenação correta das mensagens". Alternativas: (a) ordenar por timestamp físico; (b) consenso distribuído (Raft/Paxos); (c) sequenciador centralizado.

**Decisão.** Sequenciador centralizado por sala: `INCR chat:seq:{room_id}` no Redis.

**Justificativa.**
- **Timestamp físico está descartado por construção.** Relógios de instâncias EC2 divergem mesmo sob NTP (dezenas de milissegundos). Duas mensagens enviadas com 5 ms de diferença por nós distintos podem receber timestamps invertidos, e a UI mostraria a resposta antes da pergunta. É precisamente o problema que motivou o artigo de Lamport de 1978.
- **Consenso é superdimensionado.** Raft resolve ordem total *tolerante a falhas bizantinas ou de partição com múltiplos proponentes*. Aqui há um único árbitro natural (o Redis) e o requisito não é sobreviver a partição do árbitro. O custo — implementação, latência de quórum, complexidade de arguição — não se paga.
- **`INCR` é atômico e single-threaded no Redis**, portanto serializa por construção, sem lock, sem retry, sem ABA.

**Consequências.**
- (+) Ordem total determinística e trivialmente verificável (basta checar se a sequência recebida é contígua).
- (+) Uma operação de rede, sub-milissegundo.
- (−) Ponto de serialização por sala: o teto de escrita de uma sala é o teto de `INCR` do Redis (~100 k ops/s — ordens de magnitude acima de qualquer sala real).
- (−) Se o Redis cair, nenhuma mensagem nova é ordenada. Aceito: sem o Redis o Pub/Sub também não funciona, então já não há sistema. Falha coerente em vez de degradação silenciosa com ordem incorreta.

---

## ADR-004

### Emissor recebe a própria mensagem pelo Pub/Sub

**Contexto.** Ao publicar, o nó emissor poderia entregar a mensagem aos seus clientes locais imediatamente (atalho), economizando um round-trip ao Redis.

**Decisão.** O emissor **não** faz o atalho. Ele publica e aguarda a mensagem retornar pelo canal Pub/Sub, tratando-a como qualquer outro nó trataria.

**Justificativa.** O atalho criaria **dois caminhos de entrega** no sistema. Clientes conectados ao nó emissor receberiam em ordem de processamento local; clientes de outros nós, em ordem de chegada do Pub/Sub. Sob concorrência, essas ordens divergem — e a garantia de ordenação total, que é o requisito central do projeto, passaria a valer apenas *entre* nós, não *dentro* do nó emissor. Um único caminho de entrega torna a propriedade de ordenação estrutural em vez de acidental.

**Consequências.**
- (+) Ordenação uniforme por construção; o teste `test_total_order_identical_across_nodes` verifica a propriedade sem casos especiais.
- (+) Um único trecho de código de entrega — menos superfície para bug.
- (−) ~1 ms de latência adicional para o remetente. **Mitigado** pelo `ack` imediato: a UI marca a mensagem como enviada assim que recebe `seq`, sem esperar o eco.

---

## ADR-005

### Lamport **e** relógio vetorial, com papéis distintos

**Contexto.** O requisito cita "ordenação de eventos (relógios lógicos)". Bastaria Lamport.

**Decisão.** Implementar ambos, com papéis explicitamente separados: Lamport estabelece *happened-before*; o relógio vetorial detecta *concorrência*; nenhum dos dois define a ordem de entrega (isso é papel do `seq`).

**Justificativa.** O relógio escalar de Lamport tem uma limitação conhecida e frequentemente cobrada em arguição: `L(a) < L(b)` **não** implica `a → b`. Implementar apenas Lamport e afirmar que ele "ordena as mensagens" seria um erro conceitual. O relógio vetorial fecha essa lacuna ao tornar a concorrência detectável, e o cliente CLI a exibe visualmente (`⚡ concorrente`) — o que transforma um conceito abstrato em evidência observável durante a demonstração.

**Consequências.**
- (+) Cobertura conceitual completa; resposta pronta para a pergunta "por que Lamport não basta?".
- (+) Evidência visual de concorrência real na demo.
- (−) Envelope da mensagem cresce com o mapa vetorial (O(n) no número de nós). Irrelevante com 3–4 nós; documentado como limitação de escala do relógio vetorial.

---

## ADR-006

### EC2 em subrede pública sem NAT Gateway

**Contexto.** A prática recomendada é colocar instâncias de aplicação em subrede privada com NAT Gateway para saída à internet.

**Decisão.** EC2 em subrede pública com IP público; isolamento feito por Security Group.

**Justificativa.** NAT Gateway custa ~US$ 32/mês por AZ e **não** é coberto pelo Free Tier — seria o maior item de custo do projeto, superando todo o resto somado. A proteção efetiva vem do Security Group, que só aceita tráfego na porta 8000 vindo do SG do ALB; a porta da aplicação não é alcançável da internet mesmo com IP público. O ElastiCache permanece em subrede privada, sem rota para a internet.

**Consequências.**
- (+) Custo de infraestrutura dentro do Free Tier.
- (+) Acesso SSH direto para depuração durante a apresentação, sem bastion.
- (−) Desvio de boa prática de produção. **Explicitamente declarado no SDD** como decisão de contexto acadêmico, com a configuração de produção descrita — demonstrar consciência do trade-off vale mais na avaliação do que replicá-lo silenciosamente.

---

## ADR-007

### Distribuição de conexões: o sistema não depende de afinidade de sessão

**Contexto.** WebSocket é uma conexão de longa duração. A questão é se o balanceador precisa manter afinidade entre cliente e nó.

**Decisão.** **Nenhuma afinidade é necessária para correção.** No ALB, mantemos stickiness por cookie (`lb_cookie`, 1 h) apenas como estabilizador do handshake HTTP; no nginx local usamos `least_conn`, sem afinidade alguma. Ambos funcionam porque o sistema foi construído para não depender disso.

**Justificativa.** Três propriedades tornam a afinidade dispensável:
1. Uma conexão WebSocket, uma vez estabelecida, permanece no nó que a atendeu — ela não migra durante sua vida.
2. Ao reconectar, o cliente refaz `join` com o seu `last_seq` e recupera o backlog em **qualquer** nó, porque o número de sequência vive no Redis e o histórico no DynamoDB.
3. A autenticação é stateless (JWT), então nenhum nó guarda sessão.

**Correção após medição.** A configuração local usava `ip_hash` como "equivalente do stickiness". Um teste de carga revelou o defeito: como todo tráfego do host chega ao nginx com o mesmo IP de origem (o gateway da rede bridge do Docker), **273 de 273 conexões foram para um único nó**, com p95 de 25 s enquanto dois nós ficavam ociosos. Pior, três abas do navegador na mesma máquina exibiriam o mesmo `node_id` — apagando justamente a evidência visual de distribuição que a demonstração precisa mostrar. Trocado por `least_conn`, que distribui pela contagem de conexões ativas — a métrica adequada quando as conexões têm durações muito desiguais.

**O mesmo defeito reapareceu no ALB.** A correção acima tratou só o balanceador local. Na AWS, o `stickiness` por cookie (`lb_cookie`) produzia o mesmo efeito por um caminho diferente: o ALB grava o cookie `AWSALB`, e **abas de um mesmo navegador compartilham o pote de cookies** — então abrir duas abas do chat caía sempre no mesmo nó. Medido no ambiente real: sem cookie, 6 requisições atingiram 3 nós; com cookie, 4 de 4 foram para um só. Stickiness foi desligado também no ALB, nas duas infraestruturas. Depois disso, 2 conexões WebSocket caem em 2 nós distintos e 6 caem nos 3.

A lição que fica registrada: "não depender de afinidade" só vale se **nenhuma** camada a impuser. Bastou uma — primeiro o `ip_hash`, depois o cookie — para apagar a evidência de distribuição.

**Consequências.**
- (+) Conexões distribuídas de fato entre os nós; a demonstração mostra `node_id` distintos.
- (+) Após a morte de um nó, os clientes órfãos se espalham pelos sobreviventes em vez de se concentrarem.
- (+) O teste de carga passa a medir o cluster, e não um nó só.
- (−) Divergência deliberada entre a configuração local (`least_conn`) e a da AWS (stickiness por cookie). Documentada aqui e no `nginx.conf`; nenhuma das duas afeta a correção, pelos três motivos acima.

---

## ADR-008

### Persistência assíncrona fora do caminho crítico de entrega

**Contexto.** A gravação no DynamoDB poderia ser aguardada (`await`) antes da publicação no Pub/Sub, garantindo que nada seja entregue sem estar persistido.

**Decisão.** Publicar no Pub/Sub imediatamente após obter o `seq`; a gravação no DynamoDB ocorre em `asyncio.Task` paralela, com retry.

**Justificativa.** O `seq` — que é o que define a ordem — já foi atribuído atomicamente antes de qualquer um dos dois caminhos. A ordem, portanto, não depende de qual termine primeiro. Aguardar o DynamoDB adicionaria 10–20 ms a **toda** mensagem para proteger contra um cenário (falha de escrita no DynamoDB) que degrada apenas o replay de histórico, não a entrega em tempo real. Trocar latência constante de todas as mensagens por durabilidade de um caminho secundário é o trade-off errado para chat.

**Consequências.**
- (+) Latência de entrega desacoplada da latência de persistência.
- (+) DynamoDB indisponível não interrompe o chat — apenas o replay fica degradado.
- (−) Janela em que uma mensagem foi entregue mas ainda não persistiu. Se a instância morrer exatamente nessa janela, aquela mensagem não aparece no replay. Aceito e documentado: o `seq` correspondente fica visível como lacuna, e o cliente detecta e sinaliza em vez de silenciar.

---

## ADR-009

### Backlog do `join` relido até ficar contíguo, com tentativas limitadas

**Contexto.** Descoberto medindo, não projetando. Com 1.200 clientes entrando em rampa, o teste de carga acusou lacunas permanentes em **8 salas de 120** — sempre em clientes que entraram por último, sempre em números de sequência publicados no exato instante do `join`.

A causa é a janela do ADR-008 vista de outro ângulo. A persistência é assíncrona, então existe um intervalo de poucos milissegundos em que uma mensagem já foi publicada mas ainda não foi gravada. Um cliente que entra dentro dessa janela não a recebe ao vivo (a assinatura do tópico passou a valer depois da publicação) **e** não a encontra no backlog (ainda não foi gravada). Lacuna permanente, só para aquele cliente.

**Decisão.** Depois de assinar o tópico, o nó lê `current_seq` do sequenciador e exige que o backlog cubra exatamente a faixa `(last_seq, current_seq]`. Se algum número falta, espera e relê — no máximo três tentativas, com espera crescente de 40, 80 e 120 ms (`app/chat_service.py::_backlog_contiguo`).

**Justificativa.** As alternativas eram piores:

- **Aguardar a persistência antes de publicar** eliminaria a janela, mas adicionaria 10–20 ms a *toda* mensagem para resolver um problema que afeta apenas o instante do `join`. É reverter o ADR-008 pelo motivo errado.
- **Deixar por conta do `resync` do cliente** funciona, mas custa ao usuário 2 segundos de lacuna visível toda vez que ele entra em uma sala movimentada — um defeito percebido, não um detalhe interno.
- **Reler indefinidamente** travaria o `join` quando a mensagem realmente nunca for gravada (o nó de origem morreu antes de concluir a escrita).

A releitura é barata justamente porque a faixa esperada é **conhecida**: não é uma espera cega, é a verificação de uma condição precisa. No caminho normal, uma única leitura satisfaz a condição e a função retorna sem dormir.

**Consequências.**
- (+) A mesma carga passou de 112/120 para **120/120 salas íntegras**.
- (+) Latência p95 caiu de 242 ms para 18 ms — efeito colateral de o cliente não precisar mais disparar `resync` em massa.
- (+) O custo só existe quando a corrida de fato acontece.
- (−) Até 240 ms adicionais no `join`, no pior caso em que a lacuna nunca fecha.
- (−) A janela do ADR-008 **não** foi eliminada: foi reduzida ao caso em que a perda é real. Nesse caso o cliente sinaliza a lacuna em vez de silenciar — o sistema continua honesto sobre o que perdeu.
