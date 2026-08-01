# Roteiro da demonstração ao vivo — SalaViva

**Duração**: 15 min de apresentação + 5 min de arguição.
**Critério em jogo**: EC3 (demonstração prática + simulação de falha), com apoio de EC1 e EC2.

Este arquivo é operacional, não descritivo. Cada linha é algo que você **digita**, **mostra** ou
**fala**. Imprima ou deixe aberto em um monitor lateral.

**Regras de ouro (leia antes de subir no palco):**

1. Nada é digitado ao vivo sem ter sido digitado no ensaio. Tudo que está aqui foi testado.
2. Se algo travar por mais de 20 s, **vá para o Plano B** (seção 6) e siga falando. Silêncio em
   frente à turma custa mais nota do que um plano B declarado.
3. O professor pode pedir a falha **a qualquer momento**. Use a seção 0 e volte ao roteiro.
4. **Nunca** rode teste de carga ao vivo. Os números já estão medidos (seção 3, bloco 11:00).

---

## 0. SE O PROFESSOR PEDIR A FALHA AGORA

> Protocolo de interrupção. Funciona em qualquer ponto da apresentação. Leva ~60 s até a prova
> principal; a recuperação completa continua acontecendo em segundo plano enquanto você volta ao
> roteiro.

**Passo 1 — anuncie e aponte a tela** (5 s):

> "Perfeito. Este é o painel de nós — três instâncias vivas. Vou derrubar uma **de verdade**, com
> `terminate-instances`. Não é um `stop`, não é um restart de container: a instância deixa de
> existir."

**Passo 2 — dispare** (terminal 2):

```bash
./scripts/kill_node.sh --aws --url "$LB"
```

Responda `s` na confirmação. (Se preferir controle manual, o `aws ec2 terminate-instances` está
na seção 4.)

**Passo 3 — narre enquanto mede** (o script cronometra sozinho):

> "Repare em três coisas, nesta ordem: o `node_id` some do painel; os clientes reconectam em
> **outro** nó — o cabeçalho do chat muda sozinho; e o número de sequência **continua de onde
> parou**, porque ele não vive na instância que morreu, vive no Redis."

**Passo 4 — a prova de que nada se perdeu** (terminal 3):

```bash
prova geral
```

Saída esperada: `sala=geral  contiguous=True  count=44  seq=1..44`

> "`contiguous=true`. A sequência da sala não tem lacuna. Zero mensagem perdida, verificado pelo
> próprio sistema, não pelo meu olho."

**Passo 5** — volte exatamente ao ponto do roteiro em que estava. O ASG repõe o nó em 2–3 min; você
mostra o painel de novo quando ele voltar.

---

## 1. Checklist pré-apresentação (T-60 min)

Marque cada caixa. Não pule nenhuma — todas já falharam em algum ensaio.

### 1.1 Infraestrutura AWS no ar

```bash
cd ~/Desktop/Trabalho-Final-SD/infra/terraform
terraform output          # se der erro, a infra não existe: rode 'make tf-apply'
```

- [ ] `terraform output` lista as 11 saídas sem erro.
- [ ] Imagem publicada no ECR e instâncias rodando a versão certa:

```bash
aws ecr describe-images \
  --repository-name salaviva \
  --region "$(terraform output -raw region)" \
  --query 'sort_by(imageDetails,&imagePushedAt)[-1].[imageTags[0],imagePushedAt]' \
  --output text
```

### 1.2 Variáveis e atalhos nos terminais

Rode este bloco **em todos os terminais** que for usar (é a origem do `$LB`, do `$ASG` e da função
`prova` usada o tempo todo):

```bash
cd ~/Desktop/Trabalho-Final-SD
export TF=infra/terraform
export LB="http://$(terraform -chdir=$TF output -raw alb_dns_name)"
export WS="ws://$(terraform -chdir=$TF output -raw alb_dns_name)"
export ASG="$(terraform -chdir=$TF output -raw asg_name)"
export TG="$(terraform -chdir=$TF output -raw target_group_arn)"
export AWS_REGION="$(terraform -chdir=$TF output -raw region)"

prova() {
  curl -s "$LB/api/rooms/${1:-geral}/messages?limit=500" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('sala=%s  contiguous=%s  count=%s  seq=%s..%s' % (d['room_id'], d['contiguous'], d['count'], d['first_seq'], d['last_seq']))"
}

alvos() {
  aws elbv2 describe-target-health --target-group-arn "$TG" \
    --query 'TargetHealthDescriptions[*].[Target.Id,TargetHealth.State]' --output table
}
```

- [ ] `echo $LB` imprime o DNS do ALB (não vazio, sem barra no fim).
- [ ] `prova geral` responde (mesmo que `count=0` neste momento).

### 1.3 Saúde do cluster

```bash
alvos                                  # devem aparecer 3 alvos 'healthy'
curl -s "$LB/readyz" ; echo            # {"status":"ready", ...}
curl -s "$LB/api/nodes" | python3 -m json.tool | head -20
```

- [ ] 3 alvos `healthy` no target group.
- [ ] `/readyz` devolve `"status":"ready"` com `message_bus:true` e `repository:true`.
- [ ] `/api/nodes` lista `count: 3`.
- [ ] Cheque cada instância individualmente (uma delas pode estar em `initial`):

```bash
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
  --query 'AutoScalingGroups[0].Instances[*].[InstanceId,LifecycleState,HealthStatus]' --output table
```

### 1.4 Abas do navegador pré-abertas (ordem exata)

> **Armadilha nº 1 da apresentação**: o ALB usa *sticky session* por cookie. **Três abas do mesmo
> navegador compartilham o mesmo cookie e caem no MESMO nó** — a prova visual de distribuição
> desaparece. Use três "potes de cookie" diferentes.

| Aba | Onde abrir | Endereço | Login |
|---|---|---|---|
| 1 | Chrome — janela normal | `$LB/` | usuário `ana`, sala `geral` |
| 2 | Chrome — janela **anônima** | `$LB/` | usuário `bruno`, sala `geral` |
| 3 | Firefox ou Safari | `$LB/` | usuário `carla`, sala `geral` |
| 4 | Chrome — aba do painel | `$LB/dashboard` | — |

- [ ] As 3 abas de chat conectadas e o campo **"Nó que te atende"** mostrando **três `i-…`
      diferentes**. Se dois coincidirem, feche a aba, limpe cookies daquele navegador (ou abra outra
      janela anônima) e reconecte até ficarem distintos.
- [ ] Envie uma mensagem de teste em cada aba e confirme que aparece nas outras duas.
- [ ] Zoom do navegador em 125–150 % (a última fileira da sala precisa ser legível do fundo).
- [ ] Painel `/dashboard` mostrando 3 nós.
- [ ] **Apague a sala de teste**: use uma sala nova para a demo (`demo`) ou aceite que o histórico
      da `geral` já tem mensagens — é indiferente para a prova, mas decida antes.

### 1.5 Terminais posicionados

- [ ] **Terminal 1** — painel do cluster, já rodando:
      `./scripts/watch_cluster.sh --url "$LB" --intervalo 2`
- [ ] **Terminal 2** — livre, para o `kill_node.sh`. Deixe o comando **já digitado**, sem Enter.
- [ ] **Terminal 3** — cliente CLI (a evidência de ordenação):
      `uv run python client/cli/salaviva_cli.py --url "$WS" --user gabriel --room geral`
- [ ] **Terminal 4** — livre, para `prova`, `alvos` e imprevistos.
- [ ] Fonte dos terminais em **16 pt ou mais**. Tema claro se o projetor for fraco.

> **Armadilha nº 2**: o CLI exige esquema `ws://` ou `wss://` no `--url`. Passar `http://` autentica
> e **falha no handshake** com `InvalidURI`. Sempre `--url "$WS"`.
> Pelo Makefile: `make cli LB_URL=ws://localhost:8080 ARGS="--user ana"`.

### 1.6 Cliente CLI testado

```bash
uv run python client/cli/salaviva_cli.py --url "$WS" --user gabriel --room geral
```

- [ ] Aparece `conectado ao nó i-…` e a linha `entrou em #geral — nó …, último seq N`.
- [ ] Digitar uma frase produz `[seq=N | L=M | i-…] gabriel: …`.
- [ ] `/nodes` lista 3 nós; `/stats` responde.
- [ ] Encerre com `/quit` e reabra limpo antes de começar.

### 1.7 Plano B local no ar (em segundo plano, o tempo todo)

```bash
make up          # redis + dynamodb + node-a/b/c + nginx em http://localhost:8080
make ps          # 3 nós 'healthy'
```

- [ ] `curl -s http://localhost:8080/api/nodes` lista `node-a`, `node-b`, `node-c`.
- [ ] Uma aba do navegador **já aberta** em `http://localhost:8080/dashboard`, escondida atrás das
      outras. É a sua rede de segurança: se a AWS ou a internet caírem, você troca de aba e continua.

### 1.8 Higiene de apresentação

- [ ] Notificações do sistema **desligadas** (Não Perturbe).
- [ ] Slack, e-mail e mensageiros fechados.
- [ ] Notebook na tomada; Wi-Fi testado no local, não no corredor.
- [ ] Slides abertos na página 1, em outra área de trabalho (Mission Control / Alt-Tab ensaiado).
- [ ] Vídeo de backup da demo de falha acessível offline (Plano B "AWS caiu").
- [ ] `docs/img/latencia_percentis.png` e `docs/img/throughput_conexoes.png` abertos em um visualizador.

### 1.9 Checklist T-5 min (o último olhar)

```bash
alvos && curl -s "$LB/readyz" && echo && prova geral
```

- [ ] 3 alvos `healthy` · `/readyz` `ready` · `prova` responde.
- [ ] 3 abas de chat com **3 `node_id` diferentes**.
- [ ] `watch_cluster.sh` rodando no Terminal 1.
- [ ] Comando do `kill_node.sh` digitado e **sem Enter** no Terminal 2.

---

## 2. Layout de tela

Um monitor projetado + o notebook. Se houver só o projetor, use áreas de trabalho e ensaie o
Alt-Tab (nunca procure janela ao vivo).

```
┌──────────────────────────────── TELA PROJETADA ────────────────────────────────┐
│                                        │                                       │
│  NAVEGADOR (metade esquerda)           │  TERMINAL 1 — watch_cluster.sh        │
│  ┌──────────────┬──────────────┐       │  ┌──────────────────────────────────┐ │
│  │ ana          │ bruno        │       │  │ NÓ            CONEX  PUBL  LAMP  │ │
│  │ nó: i-0aa…   │ nó: i-0bb…   │       │  │ i-0aa1…         2      41    87  │ │
│  │              │              │       │  │ i-0bb2…         1      12    87  │ │
│  │ [mensagens]  │ [mensagens]  │       │  │ i-0cc3…         1       9    87  │ │
│  └──────────────┴──────────────┘       │  └──────────────────────────────────┘ │
│  (aba 3 = carla, atrás; aba 4 =        │                                       │
│   /dashboard, à frente na demo         │  TERMINAL 3 — CLI (ordenação)         │
│   de falha)                            │  ┌──────────────────────────────────┐ │
│                                        │  │ [seq=143 | L=87 | i-0aa1…] ana:… │ │
│                                        │  │ [seq=144 | L=88 | i-0bb2…] bru…  │ │
│                                        │  │   ⚡ CONCORRENTE com seq=143      │ │
│                                        │  └──────────────────────────────────┘ │
├────────────────────────────────────────┴───────────────────────────────────────┤
│  TERMINAL 2 (kill_node.sh) e TERMINAL 4 (prova/alvos): sobrepostos, chamados    │
│  à frente só quando usados. Nunca ficam visíveis por acidente.                  │
└────────────────────────────────────────────────────────────────────────────────┘
```

**Regra de foco**: em cada momento existe **uma** janela protagonista. Ao trocar, diga em voz alta
para onde a plateia deve olhar — *"agora olhem o terminal da direita"*.

**Trocas planejadas de tela:**

| Momento | Protagonista |
|---|---|
| 0:00–4:00 | Slides |
| 4:00–6:00 | Navegador (3 abas de chat) + Terminal 1 |
| 6:00–8:00 | Terminal 3 (CLI) |
| 8:00–11:00 | Aba `/dashboard` + Terminal 2, com Terminal 4 para a prova |
| 11:00–13:00 | Slides (gráficos de carga) |
| 13:00–15:00 | Slides |

---

## 3. Roteiro minuto a minuto

### 0:00 – 2:00 · Contexto e problema

**MOSTRAR**: slides 1–2 (capa, problema).

**FALAR**:

> "SalaViva: um chat em salas, em tempo real, distribuído. O problema não é 'fazer um chat' — isso é
> um WebSocket e um `dict`. O problema aparece quando o chat **não cabe em uma máquina**."
>
> "Com mais de um servidor, três coisas quebram de uma vez. Primeira: quem está no servidor A não vê
> quem está no B. Segunda: se cada servidor entregar na ordem em que recebeu, **dois usuários da
> mesma sala veem conversas diferentes** — a resposta antes da pergunta. Terceira: se um servidor
> morre, os clientes dele perdem mensagens."
>
> "Nossa meta foi resolver as três e, principalmente, **provar** que resolvemos — não afirmar."

**DIGITAR**: nada.

**SE ATRASAR**: corte a analogia; vá direto às três quebras.

---

### 2:00 – 4:00 · Arquitetura (slide 8)

**MOSTRAR**: slide 8 (diagrama de arquitetura).

**FALAR** — percorra o diagrama na ordem do caminho da mensagem:

> "Cliente fala WebSocket com o **Application Load Balancer**. Escolhemos ALB porque é o único
> balanceador da AWS que faz o *upgrade* de HTTP para WebSocket."
>
> "Atrás dele, **três EC2 t3.micro em Auto Scaling Group**. A propriedade central do sistema é esta:
> **nenhum nó conhece nenhum outro nó**. Não há comunicação nó-a-nó em lugar nenhum do código."
>
> "Eles se falam através do **ElastiCache Redis**, e o Redis faz quatro trabalhos distintos:
> `PUBLISH/SUBSCRIBE` no canal `chat:room:{id}` para o fan-out — isso é **comunicação indireta**, o
> emissor não sabe quem recebe; `INCR chat:seq:{id}`, um contador atômico que dá a **ordem total**;
> e dois `ZSET`, um de presença e um de nós vivos."
>
> "O histórico vai para o **DynamoDB**, com chave composta `(room_id, seq)` — o `seq` é *sort key*,
> então o replay de reconexão é uma única `Query` já ordenada pelo índice."
>
> "A consequência prática do desacoplamento: adicionar ou remover um nó **não exige reconfigurar
> nenhum outro**. É por isso que o Auto Scaling funciona sem orquestração — e é por isso que a
> demonstração de falha que vou fazer daqui a pouco funciona."

**DIGITAR**: nada.

**SE ATRASAR**: fale só do Redis (os 4 papéis) e do "nenhum nó conhece nenhum outro nó". O resto está
no SDD.

---

### 4:00 – 6:00 · Demo: o chat funcionando, três clientes em três nós

**MOSTRAR**: navegador com as 3 abas lado a lado + Terminal 1 (`watch_cluster.sh`).

**FALAR**:

> "Três clientes, mesma sala. Olhem o campo **'Nó que te atende'** no topo de cada um."

Aponte com o cursor, um por um:

> "`i-0aa1…`, `i-0bb2…`, `i-0cc3…`. **Três instâncias EC2 diferentes.** Não é uma máquina servindo
> três abas: são três máquinas."

Digite na aba da `ana`: `oi, quem está aí?` — e espere aparecer nas outras duas.

> "A mensagem saiu de uma instância e chegou às outras duas. Nenhum dos três servidores sabe que os
> outros existem. Ela foi publicada no tópico `chat:room:geral` do Redis e os três nós, que estão
> inscritos, receberam por difusão. **Isso é comunicação em grupo por comunicação indireta** — é o
> item central do critério EC2."

Aponte o Terminal 1:

> "Este painel lê `GET /api/nodes`, que vem do `ZSET chat:nodes` no Redis. Três nós, cada um com suas
> próprias conexões e seu próprio relógio de Lamport — e reparem que os relógios estão convergindo."

**DIGITAR** (se precisar reabrir o painel):

```bash
./scripts/watch_cluster.sh --url "$LB" --intervalo 2
```

**SE DER ERRADO**:

- Dois clientes no mesmo nó → *"o balanceador colocou dois no mesmo nó; o terceiro está em outro —
  o ponto é o mesmo"*. E siga. Não tente corrigir ao vivo.
- Mensagem não chega → dê F5 na aba (o cliente reconecta com backoff e refaz o `join` com o
  `last_seq`; nada se perde). Se persistir, Plano B local.

---

### 6:00 – 8:00 · Demo: ordenação (seq, Lamport e relógio vetorial)

**MOSTRAR**: Terminal 3 (CLI) em tela cheia ou metade da tela; abas do navegador ao lado.

**FALAR**:

> "O cliente web mostra que funciona. Este terminal mostra **por que** funciona: ele imprime os três
> carimbos de ordenação que o servidor coloca em cada mensagem."

Aponte uma linha: `[seq=143 | L=87 | i-0aa1…] ana: olá`

> "`seq` é a ordem **total** da sala, vinda do `INCR` atômico no Redis. É o **único** campo usado para
> ordenar. `L` é o relógio de **Lamport**, que estabelece *happened-before*. E há um terceiro campo
> que não aparece na linha: o **relógio vetorial**, usado para detectar concorrência."

Agora produza mensagens simultâneas. Peça a um colega (ou faça você) para enviar de **duas abas ao
mesmo tempo** — conte "três, dois, um, agora" e ambos apertam Enter:

> "Duas mensagens enviadas ao mesmo tempo, por **nós diferentes**. Nenhuma causou a outra: elas são
> **concorrentes**, e o relógio vetorial detecta isso."

Aponte a marca amarela `⚡ CONCORRENTE com seq=143`:

> "O sistema não está adivinhando quem falou primeiro — ele está **declarando** que não há relação
> causal entre as duas. Mesmo assim, todos os clientes exibem as duas na **mesma ordem**, porque o
> `seq` desempata de forma determinística. Ordem parcial de Lamport, ordem total do sequenciador:
> papéis diferentes, e é isso que o ADR-005 registra."

Feche o bloco com a limitação, antes que perguntem:

> "E o `ts`, o timestamp físico? Está no envelope, mas **nunca** é usado para ordenar. Relógios de EC2
> divergem mesmo com NTP — é exatamente o problema que motivou o artigo do Lamport em 1978."

**DIGITAR** (se precisar reabrir o CLI):

```bash
uv run python client/cli/salaviva_cli.py --url "$WS" --user gabriel --room geral
```

Para gerar tráfego contínuo em outro terminal (opcional, ajuda a mostrar seq subindo):

```bash
uv run python client/cli/salaviva_cli.py --url "$WS" --user robo --room geral --auto 4
```

**SE DER ERRADO**:

- A marca de concorrência não aparecer → *"a concorrência depende de as duas mensagens realmente se
  cruzarem no tempo; o relógio vetorial só marca quando há de fato incomparabilidade. Nesta rodada
  uma chegou antes da outra"*. Tente **uma** vez mais e siga. Não insista.
- CLI travar → `Ctrl+C`, reabra. O resumo de sessão que ele imprime ao sair também é material bom
  de mostrar.

---

### 8:00 – 11:00 · DEMO DE FALHA (o clímax)

> Dispare o `kill` **no primeiro segundo do bloco**. A prova de não-perda leva ~60 s; o resto do
> tempo você narra enquanto o ASG repõe o nó. Nunca fique esperando em silêncio.

**MOSTRAR**: aba `/dashboard` em primeiro plano, Terminal 2 ao lado, abas de chat visíveis abaixo.

**8:00 — Anuncie e dispare.**

```bash
./scripts/kill_node.sh --aws --url "$LB"
```

Confirme com `s`.

> "Vou encerrar uma instância EC2 **de verdade** — `terminate-instances`, não um restart. Depois desse
> comando essa máquina não existe mais. Escolhi esta arquitetura, em vez de Lambda, exatamente para
> poder fazer isto na frente de vocês."

**8:20 — o nó some do painel.**

Aponte o `/dashboard`:

> "Três nós… agora dois. O `node_id` que sumiu **é o próprio ID da instância EC2** — o mesmo `i-…`
> que aparece no console da AWS. Ele saiu porque o *sweeper* varre o `ZSET chat:nodes` do Redis: o nó
> parou de renovar o heartbeat e foi removido em menos de 15 segundos. **Ninguém precisou avisar
> ninguém da morte.**"

**8:40 — os clientes migraram.**

Aponte o cabeçalho das abas de chat:

> "Olhem o campo 'Nó que te atende' nesta aba: mudou sozinho. O cliente perdeu a conexão, reconectou
> com backoff pelo ALB, caiu em **outro** nó, e refez o `join` mandando o `last_seq` que ele tinha —
> recebendo de volta exatamente o que faltava. Menos de 5 segundos."

Envie uma mensagem nova em uma das abas:

> "E o chat continua funcionando, com dois nós, enquanto o terceiro está sendo recriado."

**9:00 — A PROVA de que nada se perdeu** (Terminal 4, em primeiro plano):

```bash
prova geral
```

Saída esperada: `sala=geral  contiguous=True  count=57  seq=1..57`

> "Este endpoint lê o histórico direto do DynamoDB e verifica se os números de sequência formam uma
> sequência **sem lacuna**. `contiguous=true`. Zero mensagem perdida — e reparem que o `seq`
> **continuou de onde parou**, não voltou a 1. Ele nunca esteve na instância que morreu: vive no
> Redis, e o histórico vive no DynamoDB. **A instância era descartável por projeto.**"

Se quiser mostrar o dado bruto (opcional, projeta bem):

```bash
curl -s "$LB/api/rooms/geral/messages?limit=500" | python3 -m json.tool | head -30
```

**9:30 – 10:30 — narre enquanto o ASG trabalha.** Enquanto isso, mostre a mudança de estado no ALB:

```bash
alvos
```

> "Aqui é a visão do Application Load Balancer. O alvo derrubado passou por `draining` e saiu do
> pool. O health check aponta para `/readyz`, **não** para `/healthz` — e essa distinção é o que faz
> o failover ser automático. `/healthz` diz apenas 'o processo está de pé'; um nó que perdeu o Redis
> passaria nesse teste e viraria um buraco negro recebendo tráfego que não consegue servir.
> `/readyz` verifica as dependências, então o nó degradado **se autoexclui**."

Se sobrar tempo de narração, use a tabela de causas (memorize as três primeiras linhas):

| Tempo | O que acontece | Por quê |
|---|---|---|
| ~10 s | O alvo sai do pool do ALB | `deregistration_delay = 10` no target group |
| ≤ 15 s | O `node_id` some do `/dashboard` | Sweeper do `ZSET chat:nodes` no Redis |
| ≤ 5 s | Clientes reconectam em outro nó, sem lacuna de `seq` | `seq` no Redis + histórico no DynamoDB |
| ≤ 45 s | O ALB confirma o alvo doente | `/readyz` a cada 15 s × 3 falhas |
| ~2–3 min | O ASG cria um nó novo, com `node_id` novo | `health_check_type = "ELB"` + `min_size` |

**10:30 — o substituto aparece.**

Aponte o `/dashboard` e o Terminal 1:

> "Voltamos a três nós. O ID é **novo** — não é a máquina antiga religada, é outra instância, criada
> pelo Auto Scaling, que baixou a imagem do ECR e entrou no pool sozinha. Nenhum comando meu."

Leia o relatório final do `kill_node.sh` na tela (ele imprime T1 detecção / T2 reposição / T3
quórum):

> "O script cronometrou: detecção em T1, substituto no ar em T2, capacidade restabelecida em T3.
> Tolerância a falhas **medida**, não afirmada."

**SE DER ERRADO** (cada caso com a fala pronta):

| Sintoma | O que fazer | O que falar |
|---|---|---|
| O nó não voltou até 11:00 | Siga para o bloco de escalabilidade | *"O ASG está provisionando; volto ao painel no fim. O ponto já está provado: o serviço não caiu e nada se perdeu."* Volte ao painel em 13:30. |
| `kill_node.sh` erra por credencial | Use o comando manual (seção 4, passo 3) | *"vou derrubar pelo comando direto da AWS CLI"* |
| `prova` volta `contiguous=false` | Rode de novo passados 5 s | *"a escrita no DynamoDB é assíncrona, fora do caminho crítico (ADR-008) — o `seq` já foi atribuído, a persistência está alcançando"*. Se persistir, mostre `/stats` no CLI e o selo "contígua" na UI. |
| Aba do chat não reconecta | F5 na aba | *"o cliente web reconecta com backoff; um F5 força imediatamente e o `join` com `last_seq` recupera o histórico"* |
| A AWS inteira não responde | **Plano B local**, seção 6.2 | *"vou demonstrar exatamente a mesma falha no cluster local, que tem paridade de comportamento"* |

---

### 11:00 – 13:00 · Escalabilidade: os números

**MOSTRAR**: slide com `docs/img/latencia_percentis.png` e `docs/img/throughput_conexoes.png`.

**FALAR** — cite os números medidos, com a fonte:

> "Medimos com um gerador de carga próprio, `loadtest/run_load.py`. Ele não mede o `ack`: mede o
> **eco do Pub/Sub**, correlacionado por `client_msg_id` — ou seja, a latência fim a fim de verdade,
> do envio de um cliente até a chegada em outro."

**Execução de referência** (cluster de 3 nós, `docs/carga-realista.json`):

| Métrica | Medido | Meta (NFR) |
|---|---|---|
| Conexões WebSocket simultâneas | 300 / 300 (100 %) | — |
| Distribuição pelos nós | 100 · 99 · 101 | equilibrada |
| Latência fim a fim **p95** | **37,3 ms** | < 200 ms ✅ |
| Latência fim a fim p99 | 109,4 ms | < 500 ms ✅ |
| Handshake WebSocket p95 | 13,6 ms | < 300 ms ✅ |
| Mensagens entregues/s | 1.502 (fan-out 9,45×) | ≥ 500/nó ✅ |
| **Verificação de ordem total** | **30/30 salas OK, 3.825 mensagens, 0 divergências** | idêntica ✅ |

> "O número que mais importa é o último. A ferramenta reconstrói a fila de hold-back de **cada
> cliente** e compara, sala por sala, a sequência que cada um viu na janela comum de observação.
> Trinta salas, trezentos clientes, zero divergência: **todos viram exatamente a mesma ordem**."
>
> "E olhem este detalhe: **159 mensagens chegaram fora de ordem** na rede e foram reordenadas pelo
> cliente antes de aparecerem. A rede entrega fora de ordem; o usuário nunca vê isso."

**Teto de conexões** (`docs/carga-1200.json`) — seja honesto, isso vale nota:

> "Subimos até **1.200 conexões simultâneas**: todas as 1.200 estabeleceram, distribuídas 400/401/399
> pelos três nós, com handshake p95 de 225 ms. A latência de entrega, aí, degradou muito — mas essa
> medição foi feita com o gerador de carga e os três nós **na mesma CPU do notebook**. O que ela mede
> é a saturação da minha máquina, não a do sistema. Reportamos assim de propósito: o número que a
> ferramenta cospe, com o veredito de ordem marcado como `INCONCLUSIVO` quando não há quórum para
> verificar."

**Como escala** (a parte conceitual, mais importante que os números):

> "Escalar aqui é adicionar instância ao Auto Scaling Group — e **nada** precisa ser reconfigurado,
> porque nenhum nó conhece nenhum outro. O limite conhecido é o sequenciador: uma sala serializa no
> `INCR`, que o Redis faz a ~100 mil operações por segundo. É um teto por sala, e salas são
> independentes — que é a dimensão que de fato cresce em um chat."

**DIGITAR**: nada. **Não** rode carga ao vivo.

**SE ATRASAR**: mostre só o gráfico de latência e diga as duas linhas em negrito (p95 de 37 ms e
30/30 salas com ordem idêntica).

---

### 13:00 – 15:00 · Conclusão e limitações

**MOSTRAR**: slides finais.

**FALAR**:

> "Recapitulando pelos critérios: **comunicação indireta**, com Pub/Sub no Redis e zero comunicação
> nó-a-nó. **Ordenação**, com três mecanismos de papéis distintos — Lamport para *happened-before*,
> vetorial para concorrência, `INCR` para ordem total. **Escalabilidade horizontal**, com ASG e
> nenhum acoplamento entre nós. **Tolerância a falhas**, que vocês acabaram de ver acontecer."

Se o nó tiver voltado durante este bloco, volte à aba `/dashboard`:

> "E, fechando o ciclo: o cluster está de volta a três nós."

**Limitações — declare antes que perguntem** (isso ganha ponto, não perde):

> "Quatro limitações que assumimos e documentamos:"
>
> "Primeira: o **ElastiCache é single-node**, então hoje é ponto único de falha. Foi decisão de custo
> — Multi-AZ com réplica não é Free Tier. Em produção seria replication group com failover
> automático; o código não muda, só a string de conexão."
>
> "Segunda: o Redis Pub/Sub é ***at-most-once***. Uma mensagem publicada enquanto um nó está
> desconectado é perdida **por aquele nó**. Cobrimos isso com histórico durável no DynamoDB e replay
> por `last_seq` — a durabilidade vem da camada de persistência, não do canal. E, quando o cliente
> detecta uma lacuna que não se fecha em 2 segundos, ele pede `resync`."
>
> "Terceira: as EC2 estão em **subrede pública, sem NAT Gateway**. É um desvio consciente de boa
> prática, tomado porque NAT custa cerca de 32 dólares por mês fora do Free Tier. A proteção real vem
> dos Security Groups encadeados: a porta 8000 só aceita tráfego do SG do ALB."
>
> "Quarta: o **relógio vetorial cresce em O(n)** no número de nós. Com 3 ou 4 é irrelevante; com
> centenas, o envelope da mensagem viraria um problema, e a solução conhecida seria *version vectors*
> por partição ou *dotted version vectors*."

**Encerramento**:

> "O que a gente quis provar não é que o chat funciona — é que dá pra **medir** que ele funciona.
> `contiguous=true`, ordem idêntica em 30 salas, e uma instância derrubada ao vivo sem perder uma
> mensagem. Obrigado."

---

## 4. Script exato da simulação de falha (comando a comando)

Versão manual, para quando você quiser controle total ou o `kill_node.sh` falhar. Cada passo traz a
saída esperada e a duração.

### Passo 0 — Antes de tudo (T-0, 5 s)

```bash
alvos
```

**Espera-se** (3 linhas `healthy`):

```
------------------------------------
|      DescribeTargetHealth        |
+------------------------+---------+
|  i-0aa1b2c3d4e5f6a7b   | healthy |
|  i-0bb2c3d4e5f6a7b8c   | healthy |
|  i-0cc3d4e5f6a7b8c9d   | healthy |
+------------------------+---------+
```

> Anote mentalmente **qual ID você vai matar**. Escolha o que NÃO está atendendo a aba do
> `/dashboard` — indiferente para o sistema, mas evita a tela do painel piscar durante a fala.

### Passo 1 — Listar as instâncias do ASG (5 s)

```bash
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
  --query 'AutoScalingGroups[0].Instances[*].[InstanceId,LifecycleState,HealthStatus]' --output table
```

**Espera-se**: 3 linhas com `InService` / `Healthy`.

### Passo 2 — Snapshot do "antes" (5 s)

```bash
curl -s "$LB/api/nodes" | python3 -c "import json,sys; d=json.load(sys.stdin); print('nós vivos:', d['count']); [print(' ', n['node_id'], 'conexões=', n['connections'], 'lamport=', n['lamport']) for n in d['nodes']]"
prova geral
```

**Espera-se**: `nós vivos: 3` e `contiguous=True count=N seq=1..N`. **Guarde o N** — é o "antes" da
comparação.

### Passo 3 — Matar (instantâneo)

Opção A — o script cronometrado (**preferida**):

```bash
./scripts/kill_node.sh --aws --url "$LB"
```

Ele lista as instâncias, pede confirmação (`s`), termina uma, e a partir daí mede sozinho
T1/T2/T3 imprimindo o progresso a cada 2 s.

Opção B — manual:

```bash
aws ec2 terminate-instances --instance-ids i-0aa1b2c3d4e5f6a7b \
  --query 'TerminatingInstances[0].[InstanceId,CurrentState.Name]' --output text
```

**Espera-se**: `i-0aa1b2c3d4e5f6a7b   shutting-down`

### Passo 4 — Detecção (T+10 s a T+15 s)

Olhe a aba `/dashboard`: de 3 para 2 nós. Ou, no terminal:

```bash
curl -s "$LB/api/nodes" | python3 -c "import json,sys; print('nós vivos:', json.load(sys.stdin)['count'])"
```

**Espera-se**: `nós vivos: 2` em até 15 s (sweeper do `ZSET chat:nodes`).

### Passo 5 — Migração dos clientes (T+2 s a T+5 s)

Nada a digitar: aponte o campo **"Nó que te atende"** nas abas do navegador. O valor mudou para um
`i-…` sobrevivente. Envie uma mensagem para provar que o chat segue vivo.

### Passo 6 — A PROVA de não-perda (T+30 s, 5 s)

```bash
prova geral
```

**Espera-se**: `contiguous=True` e `last_seq` **maior** que o N anotado no Passo 2 (porque você
continuou mandando mensagens).

Fallback sem `python3`:

```bash
curl -s "$LB/api/rooms/geral/messages?limit=500" | tr ',' '\n' | grep -E '"contiguous"|"count"|"first_seq"|"last_seq"'
```

**Espera-se**: `"contiguous":true`

### Passo 7 — Estado do ALB (T+45 s, 5 s)

```bash
alvos
```

**Espera-se**: o alvo morto em `draining`/`unused` ou já ausente; os outros dois `healthy`.

### Passo 8 — Reposição pelo ASG (T+2 min a T+3 min)

```bash
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
  --query 'AutoScalingGroups[0].Instances[*].[InstanceId,LifecycleState,HealthStatus]' --output table
```

**Espera-se**: uma instância com ID **novo**, passando por `Pending` → `InService`.

### Passo 9 — Quórum restabelecido (T+3 min)

```bash
curl -s "$LB/api/nodes" | python3 -c "import json,sys; print('nós vivos:', json.load(sys.stdin)['count'])"
prova geral
```

**Espera-se**: `nós vivos: 3` e `contiguous=True`. Aponte o `/dashboard`: três nós, um deles com ID
que não existia antes.

### Cronograma resumido da falha

| T | Evento | Onde se vê |
|---|---|---|
| 0 s | `terminate-instances` | Terminal 2 |
| ~2–5 s | Clientes reconectam em outro nó | Cabeçalho das abas de chat |
| ~10 s | Alvo sai do pool do ALB | `alvos` |
| ≤ 15 s | `node_id` some do painel | `/dashboard`, Terminal 1 |
| ~30 s | `contiguous=true` — prova de não-perda | Terminal 4 |
| ≤ 45 s | ALB marca o alvo como não saudável | `alvos` |
| 2–3 min | Novo nó `InService`, ID novo | `/dashboard` + ASG |

---

## 5. Arguição — perguntas prováveis e respostas preparadas

> Regra: responda em **duas frases**, pare, e ofereça o detalhe. Resposta longa demais parece
> insegurança e consome os 5 minutos.

**1. "A proposta sugeria API Gateway WebSocket + Lambda. Por que vocês não usaram?"**

> Porque o próprio critério de avaliação diz que o senhor pode pedir para derrubar uma instância EC2
> — e **arquitetura serverless não tem instância para derrubar**: a tolerância a falhas viraria uma
> afirmação sobre a plataforma da AWS, não uma propriedade do nosso sistema. Há duas razões técnicas
> a mais: Lambda com ElastiCache exige VPC attachment, que traz cold start de ~1 s, inaceitável em
> handshake de chat; e o relógio de Lamport é, por definição, **estado por processo** — em Lambda
> teríamos de externalizá-lo para o Redis a cada evento, o que descaracteriza o algoritmo. Está em
> ADR-001.

**2. "Se vocês têm Lamport, por que precisam do `seq`? Lamport não ordena?"**

> Lamport dá ordem **parcial**. Ele garante que, se `a` causou `b`, então `L(a) < L(b)` — mas **não**
> garante a recíproca: `L(a) < L(b)` não implica que `a` aconteceu antes. Dois eventos concorrentes
> em nós diferentes podem receber valores quaisquer, inclusive iguais. A interface precisa de uma
> lista linear, ou seja, ordem **total**, e isso vem do `INCR` atômico. Implementar só Lamport e
> dizer que ele "ordena as mensagens" seria um erro conceitual — é justamente por isso que
> implementamos também o relógio vetorial, para tornar a concorrência **detectável** em vez de
> escondida.

**3. "O Redis não é um ponto único de falha?"**

> Nesta configuração, **sim, e nós declaramos isso**: é single-node por decisão de custo, porque
> Multi-AZ com réplica não está no Free Tier. Mas separo duas coisas. O ponto único de falha de
> *disponibilidade* é real e a mitigação de produção é um replication group com failover automático —
> o código não muda, muda a string de conexão. O que **não** é ponto único de falha é a *correção*:
> se o Redis cai, o sistema para de ordenar mensagens novas, mas nenhuma mensagem já entregue fica
> fora de ordem, e o histórico persiste no DynamoDB. É falha coerente, não degradação silenciosa com
> ordem errada.

**4. "E se dois usuários enviarem exatamente ao mesmo tempo?"**

> "Ao mesmo tempo" no sentido físico é indistinguível — e é justamente por isso que não olhamos o
> relógio físico. As duas mensagens chegam a nós diferentes, cada nó faz `INCR chat:seq:geral`, e o
> Redis é **single-threaded**: as duas operações são serializadas por construção, sem lock e sem
> retry. Uma recebe 143 e a outra 144, e **todos os clientes de todas as instâncias veem essa mesma
> ordem**. Quem "chegou primeiro" é arbitrado, mas é arbitrado **uma vez só, no mesmo lugar, para
> todo mundo**. E o relógio vetorial marca essas duas como concorrentes — o sistema mostra na tela
> que a escolha foi arbitrária, em vez de fingir que houve causalidade.

**5. "Como isso escala para 1 milhão de usuários?"**

> Em três eixos, e o gargalo muda em cada um. **Conexões**: são o eixo fácil — mais instâncias no
> ASG, sem reconfigurar nada, porque nenhum nó conhece nenhum outro; a ~500 sockets por `t3.micro`,
> 1 milhão são ~2.000 nós, ou muito menos com instâncias maiores. **Fan-out**: cada nó recebe hoje
> **todas** as mensagens de todas as salas em que tem alguém; nessa escala eu trocaria por
> particionamento — o Redis Cluster faz *sharding* dos canais por hash slot, e cada nó assina só as
> salas que atende. **Ordenação**: o `INCR` é por sala, então salas são independentes; o teto é uma
> sala com mais escritas do que ~100 mil por segundo, o que não é chat, é broadcast — e aí o desenho
> certo é outro (uma sala com um milhão de leitores e poucos escritores vira um problema de CDN, não
> de ordenação).

**6. "Por que não usaram simplesmente o timestamp da mensagem para ordenar?"**

> Porque relógios de máquinas diferentes divergem, mesmo com NTP — na casa de dezenas de
> milissegundos entre instâncias EC2. Duas mensagens enviadas com 5 ms de diferença por nós distintos
> podem receber timestamps **invertidos**, e a interface mostraria a resposta antes da pergunta. É
> literalmente o problema que motivou o artigo do Lamport em 1978. O campo `ts` existe no nosso
> envelope, mas está documentado no protocolo como **"nunca usar para ordenar"** — é informativo.

**7. "Por que Redis Pub/Sub e não Kafka? Ou SNS/SQS?"**

> Por semântica e por latência. **SQS entrega a uma fila, para um consumidor** — isso é balanceamento
> de carga, não difusão; para fazer fan-out eu precisaria de uma fila por nó, criada e destruída
> conforme o ASG escala, o que reintroduz exatamente o acoplamento nó-a-nó que a comunicação indireta
> existe para eliminar. E SNS→SQS→polling custa de 100 a 500 ms fim a fim; chat com 300 ms de atraso
> é percebido como quebrado. **Kafka** resolveria durabilidade e ordem por partição, mas: o MSK não é
> Free Tier, ele é ordenado *por partição* e eu precisaria de uma partição por sala para ter a
> garantia que quero, e a latência de um chat não precisa de log durável no caminho crítico. Nós
> escolhemos **canal rápido e não-durável + armazenamento durável separado**, que é o trade-off
> registrado no ADR-002. Se o requisito fosse *exactly-once* com replay de dias, Kafka seria a
> escolha certa.

**8. "E se a rede particionar?"**

> Depende de onde. **Cliente separado do ALB**: a conexão morre, o cliente reconecta com backoff e
> refaz o `join` mandando o `last_seq`; recupera exatamente o que perdeu. **Um nó separado do
> Redis**: aquele nó reprova no `/readyz`, o ALB o tira do pool em até 45 s e ele para de receber
> tráfego — ele **se autoexclui** em vez de aceitar mensagens que não consegue ordenar. **Partição
> dentro da VPC entre AZs**: o Redis fica de um lado; os nós do outro lado ficam indisponíveis, e o
> serviço continua com a fração que enxerga o Redis. Em nenhum desses casos existe *split-brain* de
> ordenação, porque só há **um** sequenciador — não há dois lados atribuindo `seq` em paralelo. O
> preço disso é a resposta da próxima pergunta.

**9. "Onde está o CAP nesse sistema?"**

> Somos **CP com relação à ordenação**, e AP com relação à leitura de histórico. O sequenciador é
> único: se um nó não alcança o Redis, ele **recusa** mensagens novas em vez de inventar uma ordem
> local — abrimos mão de disponibilidade para preservar consistência. Já o histórico no DynamoDB
> continua legível durante uma partição do Redis, e a presença degrada para "quem eu vejo daqui",
> que é o comportamento aceitável. A escolha é deliberada: em um chat, a mensagem que não chega é um
> incômodo; a mensagem que aparece na ordem errada, para um usuário e não para o outro, é um bug
> visível e indefensável.

**10. "Como vocês garantem que não perderam mensagem?"**

> Não garantimos por confiança — **verificamos**. São quatro camadas. **Um**: o `seq` vem de um `INCR`
> atômico, então a sequência de uma sala é contígua por construção e uma lacuna é **detectável**.
> **Dois**: o cliente tem uma fila de hold-back que só renderiza o que é contíguo e, se a lacuna
> persistir 2 segundos, pede `resync` — o mesmo código do servidor e da suíte de testes. **Três**: na
> reconexão, o `join` leva o `last_seq` e o DynamoDB devolve pela chave `(room_id, seq)` exatamente
> o que faltava. **Quatro**: o endpoint `/api/rooms/{sala}/messages` devolve o campo `contiguous` —
> é o que projetei na tela agora há pouco, depois de matar uma instância. E o teste de carga verifica
> mais: ele compara a sequência vista por cada um dos 300 clientes, sala a sala; deu 30 de 30 salas
> idênticas, zero divergência.

### Perguntas de segunda linha (prepare, podem vir)

**"Por que ALB e não NLB?"**
> O ALB é o único ELB que faz o upgrade HTTP/1.1 → WebSocket e health check de camada 7 em
> `/readyz`. O NLB passaria TCP puro e não conseguiria remover do pool um nó que está de pé mas com o
> Redis inacessível.

**"Por que `/readyz` e não `/healthz` no health check?"**
> `/healthz` diz só que o processo respira. Um nó que perdeu o Redis passaria nele e continuaria
> recebendo conexões que não consegue servir — um buraco negro. `/readyz` checa Redis e DynamoDB,
> então o nó degradado se autoexclui. Essa distinção é o que torna o failover automático.

**"O emissor não poderia entregar a própria mensagem localmente e economizar um round-trip?"**
> Poderia, e nós decidimos que não (ADR-004). O atalho criaria **dois caminhos de entrega**: clientes
> do nó emissor veriam ordem de processamento local e clientes dos outros nós veriam ordem do
> Pub/Sub. A garantia de ordem total passaria a valer *entre* nós, não *dentro* do emissor. Custa
> ~1 ms, e a UI não sente porque o `ack` volta imediatamente com o `seq`.

**"E se a mesma mensagem for enviada duas vezes?"**
> `SET NX chat:dedupe:{client_msg_id}` com TTL de 5 min. O segundo `send` recebe o `ack` original com
> `duplicate: true` e **não** gera novo `seq`.

**"Por que EC2 em subrede pública? Isso não é inseguro?"**
> É um desvio consciente de boa prática: NAT Gateway custa ~US$ 32/mês e não é Free Tier — seria o
> maior item de custo do projeto. A proteção efetiva vem do Security Group: a porta 8000 só aceita
> tráfego do SG do ALB, então a aplicação não é alcançável da internet mesmo com IP público. O Redis
> fica em subrede privada, sem rota para a internet. Está declarado no SDD como ADR-006, com a
> configuração de produção descrita.

**"Vocês testaram falha bizantina?"**
> Não, e está declarado fora de escopo no SDD. O modelo assumido é *fail-stop*: nós confiam uns nos
> outros dentro da VPC, isolados por Security Group. Tolerância bizantina exigiria consenso com
> quórum de 3f+1, que é desproporcional ao requisito.

**"Quanto custa isso rodando?"**
> Cerca de US$ 0,07 por hora fora do Free Tier — três `t3.micro`, um ALB e um `cache.t3.micro`. Por
> isso o `terraform destroy` está documentado e vai ser executado logo depois desta apresentação.

---

## 6. Plano B

### 6.1 Se a internet do local cair

O cluster local já está no ar desde o checklist (item 1.7). Ele tem **paridade de comportamento**:
mesmo código, mesmo Redis, mesmo DynamoDB (Local), mesmo `/readyz`.

```bash
export LB=http://localhost:8080
export WS=ws://localhost:8080
make ps                     # confirme node-a/b/c 'healthy'
```

Abra `http://localhost:8080` (3 abas) e `http://localhost:8080/dashboard`.

**Fale isto, não esconda**:

> "Perdemos a rede. Vou demonstrar no cluster local, que é o mesmo código com paridade de
> comportamento: três nós, o mesmo Redis, o mesmo `/readyz`. O que muda é que o Auto Scaling vira a
> política `restart: unless-stopped` do Compose."

A demo de falha local:

```bash
./scripts/kill_node.sh --segurar 25
```

O `--segurar 25` mantém o nó fora por 25 s — tempo para a plateia ver o painel encolher antes da
recuperação. Depois:

```bash
prova geral      # com $LB=http://localhost:8080
```

> Aqui os três clientes **caem em nós diferentes** naturalmente: o nginx local usa `least_conn`, sem
> afinidade por cookie. Não há a armadilha do ALB.

### 6.2 Se a AWS falhar (mas houver internet)

1. Tente uma vez: `alvos` e `curl -s "$LB/readyz"`. Dê **30 s**, não mais.
2. Se não voltar, vá para o cluster local (6.1) com a fala acima.
3. Se nem o local subir, use o **vídeo de backup** da demo de falha e narre por cima dele, apontando
   os mesmos pontos do bloco 8:00–11:00.

### 6.3 Se o nó não voltar (ASG não repõe)

Diagnostique em 20 s, sem cavar:

```bash
aws autoscaling describe-scaling-activities --auto-scaling-group-name "$ASG" \
  --max-items 3 --query 'Activities[*].[StatusCode,StatusMessage]' --output table
```

**Fale**:

> "O Auto Scaling ainda está provisionando — leva de dois a três minutos porque a instância precisa
> subir o SO, instalar o Docker, baixar a imagem do ECR e ser aprovada no health check. O ponto já
> está demonstrado: o serviço continuou com dois nós e nenhuma mensagem se perdeu."

Se precisar forçar (só se sobrar tempo, no fim):

```bash
aws autoscaling set-desired-capacity --auto-scaling-group-name "$ASG" --desired-capacity 3
```

**Nunca** rode `terraform apply` durante a apresentação.

### 6.4 Se um cliente travar

| Sintoma | Ação |
|---|---|
| Aba do chat congelada | **F5**. O cliente reconecta com backoff e refaz o `join` com `last_seq` — nada se perde. |
| Aba não reconecta após F5 | Feche a aba, abra outra (janela anônima) e faça login de novo. Não gaste mais de 15 s. |
| CLI travado | `Ctrl+C` e reabra: `uv run python client/cli/salaviva_cli.py --url "$WS" --user gabriel --room geral` |
| CLI com `InvalidURI` | Você passou `http://`. Use `ws://`. |
| `watch_cluster.sh` mostrando "nenhuma resposta" | Normal durante a janela de falha. Se persistir > 30 s, `Ctrl+C` e rode de novo. |
| Todos os clientes no mesmo nó | Não corrija ao vivo. Diga *"o balanceador agrupou; o ponto de distribuição já foi mostrado"* e siga. |

### 6.5 Se o projetor perder a imagem

Continue **falando** enquanto reconecta. Tenha a fala pronta: *"enquanto a imagem volta, deixa eu
adiantar o que vocês vão ver: três instâncias distintas atendendo a mesma sala"*. Silêncio é o único
erro irrecuperável.

---

## 7. Tabela de comandos de emergência (cola rápida)

**Preparação do terminal** (rode primeiro, em qualquer terminal novo):

```bash
cd ~/Desktop/Trabalho-Final-SD && export TF=infra/terraform
export LB="http://$(terraform -chdir=$TF output -raw alb_dns_name)"
export WS="ws://$(terraform -chdir=$TF output -raw alb_dns_name)"
export ASG="$(terraform -chdir=$TF output -raw asg_name)"
export TG="$(terraform -chdir=$TF output -raw target_group_arn)"
```

| O que preciso | Comando |
|---|---|
| **Prova de não-perda** | `curl -s "$LB/api/rooms/geral/messages?limit=500" \| tr ',' '\n' \| grep '"contiguous"'` |
| Nós vivos (contagem) | `curl -s "$LB/api/nodes" \| tr ',' '\n' \| grep '"count"'` |
| Nós vivos (detalhe) | `curl -s "$LB/api/nodes" \| python3 -m json.tool` |
| Saúde do cluster | `curl -s "$LB/readyz"` |
| Alvos do ALB | `aws elbv2 describe-target-health --target-group-arn "$TG" --query 'TargetHealthDescriptions[*].[Target.Id,TargetHealth.State]' --output table` |
| Instâncias do ASG | `aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" --query 'AutoScalingGroups[0].Instances[*].[InstanceId,LifecycleState,HealthStatus]' --output table` |
| **Derrubar um nó (AWS)** | `./scripts/kill_node.sh --aws --url "$LB"` |
| Derrubar manual | `aws ec2 terminate-instances --instance-ids i-XXXX` |
| Derrubar um nó (local) | `./scripts/kill_node.sh --segurar 25` |
| Painel do cluster | `./scripts/watch_cluster.sh --url "$LB" --intervalo 2` |
| Cliente CLI | `uv run python client/cli/salaviva_cli.py --url "$WS" --user gabriel --room geral` |
| CLI gerando carga | `uv run python client/cli/salaviva_cli.py --url "$WS" --user robo --room geral --auto 4` |
| Subir o Plano B local | `make up` · depois `export LB=http://localhost:8080 WS=ws://localhost:8080` |
| Plano B **sem Docker** (1 nó) | `SALAVIVA_REDIS_URL=memory:// SALAVIVA_PERSISTENCE_ENABLED=true SALAVIVA_NODE_ID=node-plano-b uv run uvicorn salaviva.main:app --host 0.0.0.0 --port 8000` |
| Forçar capacidade do ASG | `aws autoscaling set-desired-capacity --auto-scaling-group-name "$ASG" --desired-capacity 3` |
| Logs de uma instância | `aws logs tail "$(terraform -chdir=$TF output -raw cloudwatch_log_group)" --since 10m --follow` |
| Estado local dos nós | `make ps` · logs: `make logs SERVICO=node-b` |
| **DEPOIS da apresentação** | `make teardown` (digite `DESTRUIR` para confirmar) |

> **Duas armadilhas, de novo, porque são as que mordem:**
> 1. CLI só aceita `--url ws://…` ou `wss://…`. Com `http://` ele autentica e falha no handshake.
> 2. Abas do mesmo navegador compartilham o cookie de stickiness do ALB → caem no **mesmo** nó. Use
>    janela normal + janela anônima + outro navegador.

> **Não esqueça**: rode `make teardown` no mesmo dia. Três `t3.micro` + ALB + ElastiCache esquecidos
> custam ~US$ 50/mês.
