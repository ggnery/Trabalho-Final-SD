# Teste de carga do SalaViva

Gera os números e os gráficos da apresentação — e, mais importante, **prova que
a ordem total sobrevive à carga**.

| Arquivo | Papel |
|---|---|
| `run_load.py` | abre N conexões WebSocket, mede e verifica; escreve um JSON |
| `plot_results.py` | lê o JSON e desenha os dois PNGs de `docs/img/` |

A separação é intencional: o teste contra a AWS roda uma vez (custa tempo e
capacidade), enquanto os gráficos são redesenhados quantas vezes for preciso a
partir do mesmo arquivo de resultado — inclusive na véspera da apresentação, sem
subir nada.

---

## O que este teste mede

| Métrica | Como é medida | Meta do projeto |
|---|---|---|
| **Latência fim a fim** | do `send` até o **eco da própria mensagem chegar pelo Pub/Sub**, correlacionado por `client_msg_id` | p95 < 200 ms · p99 < 500 ms |
| **Handshake WebSocket** | do início da conexão até o frame `welcome` (inclui validação do JWT) | p95 < 300 ms |
| **Throughput** | mensagens entregues por segundo, na janela estável | ≥ 500 msg/s por nó |
| **Conexões** | estabelecidas × falhas, com motivo, e distribuição por `node_id` | ≥ 1.000 no cluster |
| **ORDEM TOTAL** | comparação da sequência de `seq` entre **todos** os clientes de cada sala | `OK` |

A latência é medida contra o **eco**, não contra o `ack`. O `ack` é produzido
localmente pelo nó que recebeu o `send` e não atravessa o barramento: medi-lo
reportaria um número bonito e irrelevante. O eco é o caminho real
(`send → INCR → PUBLISH → Redis → SUBSCRIBE → broadcast`), que é o que o
usuário sente e o que o ADR-004 assume.

---

## Antes de rodar: `ulimit -n`

**Cada conexão WebSocket consome um descritor de arquivo.** O padrão do macOS é
256 — um teste de 500 clientes falharia por volta da conexão 250 com
`Too many open files`, e o sintoma é fácil de confundir com o servidor recusando
conexões, que é justamente o que o teste deveria estar medindo.

O `run_load.py` tenta elevar o limite flexível sozinho (até o teto rígido) e
avisa se não conseguir. Se aparecer o aviso, eleve manualmente **na mesma sessão
do shell**:

```bash
ulimit -n 4096          # linux e macOS; vale só para este shell
ulimit -n               # confere
```

Se o teto rígido for baixo (`ulimit -Hn`), no macOS:

```bash
sudo launchctl limit maxfiles 65536 200000
```

Regra prática: `ulimit -n` ≥ `--clients` + 256.

O limite efetivo detectado vai para o JSON em `meta.limite_descritores` — se um
resultado tiver muitas falhas de conexão, é o primeiro campo a conferir antes de
culpar o servidor.

---

## Dependências

`websockets` já é dependência do projeto. Para os gráficos:

```bash
uv sync --extra loadtest          # instala matplotlib
# ou, sem uv:
pip install matplotlib
```

O ponto de entrada canônico é o **módulo**, não o arquivo:

```bash
python -m loadtest.run_load --help
python -m loadtest.plot_results --help
```

Rode a partir da raiz do repositório, para que o pacote `loadtest` seja
importável.

---

## Como rodar

### 1. Contra um nó local autônomo (sem Redis, sem AWS)

O caminho mais rápido para verificar que o teste funciona:

```bash
SALAVIVA_REDIS_URL=memory:// SALAVIVA_PERSISTENCE_ENABLED=false \
  uv run uvicorn salaviva.main:create_app --factory --port 8000

# em outro terminal
uv run python -m loadtest.run_load --url http://localhost:8000 \
    --clients 300 --rooms 10 --rate 0.5 --ramp 15 --duration 30
```

Um nó só, tudo em memória: os números de latência ficam na casa de 1–5 ms e não
representam a nuvem. O que este modo prova é a **corretude do teste**, não o
desempenho do sistema.

### 2. Contra o cluster local (Docker Compose — 3 nós + Redis)

É o ensaio fiel: três nós, Redis real, clientes da mesma sala distribuídos entre
instâncias diferentes. Só aqui a verificação de ordem passa a ser interessante,
porque passa a haver concorrência **entre nós** disputando o `INCR`.

```bash
make up                 # ou: docker compose up -d

uv run python -m loadtest.run_load --url http://localhost:8080 \
    --clients 600 --rooms 20 --rate 0.5 --ramp 30 --duration 60
```

Use a porta do balanceador (nginx), não a de um nó específico — o objetivo é
espalhar as conexões. Confira em `conexoes.por_no` no JSON que os três nós
apareceram; se só um aparecer, o balanceador não está distribuindo e o teste
perde o sentido.

### 3. Contra a AWS (o resultado que vai para os slides)

```bash
uv run python -m loadtest.run_load \
    --url http://SEU-ALB-1234567890.us-east-1.elb.amazonaws.com \
    --clients 1200 --rooms 20 --rate 0.5 \
    --ramp 60 --duration 120 --drain 10 \
    --out loadtest/resultado.json

uv run python -m loadtest.plot_results     # escreve em docs/img/
```

Rode de uma máquina com banda estável. **A rampa longa é obrigatória aqui**: o
ALB registra alvos de forma incremental, e 1.200 conexões abertas de uma vez
seriam rejeitadas pela borda antes de chegarem à aplicação — o teste mediria o
ALB, não o SalaViva.

Se o alvo estiver atrás de HTTPS, passe `--url https://…`; o esquema `wss` é
derivado sozinho.

### 4. Junto com a demonstração de falha (critério EC3)

```bash
uv run python -m loadtest.run_load --url http://SEU-ALB… \
    --clients 800 --rooms 20 --duration 180 --reconnect &

# com a carga em regime, derrube uma instância:
scripts/kill_node.sh          # ou termine a EC2 pelo console
```

Com `--reconnect`, cada cliente derrubado reconecta com backoff e refaz o `join`
informando o último `seq` que viu — e o backlog devolvido no `joined` entra na
verificação de ordem. O que se espera do relatório:

- `conexoes.quedas_durante_o_teste` ≈ número de clientes que estavam no nó
  morto — a falha realmente aconteceu;
- `conexoes.reconexoes_falhas` > 0 durante a janela em que o nó esteve fora (é
  esperado, não é defeito) e `conexoes.sessoes_abertas` > `--clients`, prova de
  que houve reconexão;
- `conexoes.por_no` muda: os clientes migram para os nós sobreviventes;
- **`ordem.veredito` continua `OK`** — nenhuma mensagem foi perdida nem
  duplicada durante a queda.

Esse último item é a evidência de FR-8 e do requisito de zero perda. Sem
`--reconnect`, os clientes do nó morto simplesmente somem e o teste mede quantas
conexões o nó segurava — também útil, mas é outra pergunta.

---

## Escolhendo a carga (leia antes de aumentar `--rate`)

O tráfego que o cluster processa **não** é `clients × rate`: cada mensagem
publicada é entregue a todos os membros da sala.

```
publicadas/s ≈ clients × rate
entregues/s  ≈ clients × rate × (clients / rooms)
```

Com os padrões (500 clientes, 20 salas, 0,5 msg/s): 250 publicadas/s e
**6.250 entregues/s**. Dobrar `--rate` dobra os dois; **reduzir `--rooms` pela
metade dobra só o segundo**. Perfis sugeridos:

| Objetivo | Comando |
|---|---|
| Verificar que tudo funciona | `--clients 100 --rooms 5 --rate 1 --ramp 10 --duration 20` |
| Número de escalabilidade (slide) | `--clients 1200 --rooms 20 --rate 0.5 --ramp 60 --duration 120` |
| Estresse de fan-out (poucas salas) | `--clients 600 --rooms 3 --rate 1 --ramp 30 --duration 60` |
| Estresse de conexões (pouco tráfego) | `--clients 2000 --rooms 50 --rate 0.1 --ramp 120 --duration 60` |

`--rate` acima de **20 msg/s** por cliente esbarra no rate limit do servidor
(FR-13): o teste passa a medir o rejeitador. O script avisa quando isso
acontece, e os frames recusados aparecem em `mensagens.erros_do_protocolo`.

---

## Argumentos

| Argumento | Padrão | Para que serve |
|---|---|---|
| `--url` | `http://localhost:8000` | base do serviço; aceita `http`, `https`, `ws`, `wss` |
| `--clients` | `500` | conexões WebSocket concorrentes |
| `--rooms` | `20` | salas entre as quais distribuir os clientes |
| `--rate` | `0.5` | mensagens por segundo **por cliente** |
| `--ramp` | `30` | segundos para abrir todas as conexões |
| `--duration` | `60` | janela estável de medição, **após** a rampa |
| `--drain` | `5` | segundos de escuta após o último envio |
| `--out` | `loadtest/resultado.json` | arquivo JSON de resultado |
| `--payload` | `64` | bytes de preenchimento por mensagem |
| `--reconnect` | desligado | reconectar e refazer o `join` ao cair |
| `--room-prefix` / `--user-prefix` | `carga` | nomes de sala e de usuário |
| `--connect-timeout` | `20` | tempo limite de handshake |
| `--login-concurrency` | `32` | logins HTTP simultâneos na preparação |
| `--max-divergencias` | `20` | divergências detalhadas no relatório |
| `--seed` | `42` | semente do jitter (reprodutibilidade) |
| `--quiet` | desligado | silencia o progresso |

A duração total de uma execução é `ramp + duration + drain`. Os percentis são
calculados **só sobre a janela estável**: incluir a rampa misturaria a latência
de um cluster com 40 conexões com a de um cluster com 1.200 e produziria um p95
que não descreve nenhum dos dois estados.

---

## Lendo o resultado

O resumo sai no terminal; o JSON tem tudo. As seções que importam:

### `ordem` — o veredito

```json
"ordem": {
  "veredito": "OK",
  "salas_verificadas": 20,
  "salas_ok": 20,
  "mensagens_verificadas": 71880,
  "chegadas_fora_de_ordem": 4127,
  "divergencias": []
}
```

Três vereditos possíveis:

- **`OK`** — em toda sala, todos os clientes viram exatamente a mesma sequência
  de `seq`, contígua e sem repetição. É a prova de FR-5 sob carga.
- **`DIVERGENTE`** — há pelo menos um cliente que viu algo diferente dos demais.
  `divergencias` traz sala, cliente, nó e quais `seq` faltaram ou sobraram.
  Isso é **falha de correção**, não número ruim.
- **`INCONCLUSIVO`** — nenhuma sala teve dois ou mais clientes com faixa de
  observação em comum. Aumente `--clients`, reduza `--rooms` ou alongue
  `--duration`.

`chegadas_fora_de_ordem` **não é erro** — é diagnóstico, e alto é bom sinal.
Conta quantas vezes uma mensagem chegou ao cliente com `seq` menor que a
anterior, ou seja, quantas vezes o Pub/Sub entregou fora de ordem e a fila de
hold-back precisou reordenar. Um número alto com veredito `OK` é exatamente a
demonstração de que a ordenação **não** depende da ordem de chegada da rede: ela
vem do `seq` do sequenciador. Em um nó só esse contador costuma ser zero, porque
não há concorrência entre nós para produzir inversão.

Como a verificação funciona, em três passos:

1. cada cliente grava a **ordem de chegada crua** dos `seq` que recebeu (a
   linha de base é o `last_seq` devolvido no `join`, então o histórico anterior
   ao teste é ignorado);
2. o script reproduz a fila de hold-back do protocolo — ordenar por `seq` e
   descartar duplicatas — obtendo o que aquele cliente **teria renderizado**;
3. dentro da **janela comum** da sala (do maior `seq` inicial ao menor `seq`
   final entre os clientes), as listas de todos os clientes têm de ser
   idênticas e contíguas.

A janela comum existe porque os clientes entram em instantes diferentes — a
rampa garante isso. Comparar faixas desiguais acusaria divergência onde há
apenas janelas de observação distintas.

### `conexoes.por_no` — a prova de distribuição

```json
"por_no": { "i-0a1b2c": 401, "i-0c2d3e": 399, "i-0e3f4a": 396 }
```

Mostra quantas conexões cada instância EC2 atendeu. É a evidência de que os
clientes da mesma sala estavam em nós físicos diferentes — sem isso, a
verificação de ordem seria trivial (um processo único não tem como divergir de
si mesmo). Se um `node_id` só aparecer, revise a política do ALB.

### `mensagens.sem_eco_ao_final`

Mensagens enviadas cujo eco não voltou até o fim do dreno. Alguns poucos são
normais (as enviadas no último instante). Um número grande indica perda real ou
saturação — cruze com `erros_do_protocolo`, onde `rate_limited` e
`service_unavailable` aparecem discriminados.

### `metas` e código de saída

O processo termina com **0** quando todas as metas foram atingidas e **1** caso
contrário — inclusive quando a única falha é `ordem_total`. Isso permite usar o
teste como porta de qualidade em CI:

```bash
uv run python -m loadtest.run_load --url http://localhost:8000 \
    --clients 200 --rooms 10 --ramp 10 --duration 20 --quiet || exit 1
```

---

## Gráficos

```bash
uv run python -m loadtest.plot_results                      # docs/img/, tema claro
uv run python -m loadtest.plot_results --tema escuro        # sufixo _escuro
uv run python -m loadtest.plot_results --json outro.json --out-dir /tmp --dpi 200
```

- **`latencia_percentis.png`** — p50, p95 e p99 com a linha de meta em 200 ms.
  Quando o sistema fica muito abaixo do alvo, a linha sairia da escala e
  achataria as três barras em um traço no chão; nesse caso o gráfico troca a
  linha por um selo dizendo quantas vezes o resultado está abaixo da meta.
- **`throughput_conexoes.png`** — dois painéis empilhados com o mesmo eixo de
  tempo: throughput em cima, conexões ativas embaixo. São dois painéis, e não
  dois eixos Y no mesmo gráfico, porque escalas diferentes sobrepostas fazem o
  leitor enxergar uma correlação que o alinhamento arbitrário das escalas
  inventou.

Ambos trazem no rodapé a procedência (alvo, conexões, data) e o veredito de
ordem — um gráfico de apresentação sem procedência não se defende na arguição.

---

## Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `nenhum login bem-sucedido` | serviço fora do ar ou URL errada | `curl $URL/healthz` |
| Muitas falhas `OSError` | limite de descritores | veja a seção `ulimit -n` |
| Falhas `TimeoutError` na rampa | rampa curta demais para o alvo | aumente `--ramp` |
| `rate_limited` em `erros_do_protocolo` | `--rate` acima de 20 msg/s | reduza `--rate` |
| `veredito: INCONCLUSIVO` | poucos clientes por sala | reduza `--rooms` ou aumente `--clients` |
| Latência alta só no início | medição pegou a rampa | confirme que os percentis vêm de `latencia_ms` (janela estável), não de `latencia_ms_execucao_completa` |
| Só um `node_id` em `por_no` | balanceador não distribuiu | use a URL do ALB/nginx, não a de um nó |
