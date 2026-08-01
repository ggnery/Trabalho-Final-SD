# Slides — SalaViva

Apresentação final da disciplina de Sistemas Distribuídos, no formato exigido
por `orientacoes/Modelo_Apresentacao_Projeto_Disciplina.pdf`: **10 slides**,
15 minutos + 5 de arguição.

| Arquivo | O que é |
|---|---|
| `apresentacao.html` | O deck. Autocontido — sem CDN, sem fonte externa, sem instalação. É este que se projeta. |
| `apresentacao.md` | O mesmo conteúdo em markdown, com as **notas do apresentador** e o tempo alvo de cada slide. É este que se estuda. |
| `README.md` | Este arquivo. |

---

## Antes de apresentar

### 1. Preencher os três campos pendentes

Há exatamente **três** placeholders, no slide 1, presentes nos dois arquivos:

```
{{INTEGRANTES}}   {{PROFESSOR}}   {{DATA}}
```

Substituir com um editor de texto qualquer, ou de uma vez:

```bash
cd slides
NOMES="Fulano de Tal, Beltrano da Silva e Ciclano Souza"
PROF="Prof. Dr. Nome do Professor"
QUANDO="12 de agosto de 2026"

for f in apresentacao.html apresentacao.md; do
  sed -i '' "s/{{INTEGRANTES}}/$NOMES/; s/{{PROFESSOR}}/$PROF/; s/{{DATA}}/$QUANDO/" "$f"
done
grep -c "{{" apresentacao.html apresentacao.md   # precisa dar 0 nos dois
```

> No Linux o `sed` é `sed -i` (sem as aspas vazias).

### 2. Conferir os números do slide 10

Os números projetados (`1.200` conexões, `37 ms` de p95, ordem total `OK`) vêm
do **cluster local de 3 nós**, e o slide diz isso no rodapé. Se der tempo de
rodar a carga contra a AWS antes da apresentação, atualize os quatro cartões e
troque “cluster de 3 nós” pelo ambiente medido:

```bash
uv run python -m loadtest.run_load \
  --url http://SEU-ALB.us-east-1.elb.amazonaws.com \
  --clients 1200 --rooms 60 --rate 0.3 --ramp 60 --duration 120
```

Números medidos na nuvem valem mais na arguição do que números medidos no
notebook. Se não der tempo, **não maquie**: a ressalva já está no slide.

### 3. Ensaiar com o cronômetro do próprio deck

Tecla `C` inicia o cronômetro no canto superior direito. Ele fica verde,
passa a âmbar aos 13:00 e a vermelho aos 15:00. Tecla `R` zera.

---

## Como apresentar

Abrir `apresentacao.html` com um duplo clique — funciona direto do sistema de
arquivos, em qualquer navegador moderno, sem servidor e sem internet.

Depois: `F` para tela cheia, `C` para começar o cronômetro, e seguir.

| Tecla | Ação |
|---|---|
| `→` `↓` `Espaço` `PgDn` | Próximo slide |
| `←` `↑` `PgUp` | Slide anterior |
| `Home` / `End` | Primeiro / último |
| `1`…`9`, `0` | Pular direto para o slide (`0` = slide 10) |
| `F` | Tela cheia |
| `T` | Alternar tema **escuro ↔ claro** |
| `C` / `R` | Cronômetro: inicia-pausa / zera |
| `P` | Abrir a impressão (exportar PDF) |
| `?` | Lista de atalhos · `Esc` fecha |

Também dá para clicar nas barrinhas do trilho inferior e arrastar o dedo para
os lados em tela sensível ao toque. O endereço guarda o slide (`…#7`): recarregar
não perde o lugar.

**Se a sala tiver projetor fraco ou muita luz, aperte `T`.** O tema claro tem
contraste maior em superfícies lavadas. A escolha fica salva no navegador.

**Um segundo monitor**: abra o `apresentacao.md` no notebook (as notas do
apresentador ficam abaixo de cada slide) e projete o HTML em tela cheia.

---

## Exportar para PDF

Pelo próprio navegador — o deck já tem folha de estilo de impressão que gera
**uma página por slide**, em paisagem e no tema claro (economiza tinta e fica
legível impresso).

1. Abrir `apresentacao.html` no **Chrome** ou **Edge**.
2. `Ctrl/Cmd + P` (ou tecla `P`).
3. Configurar:
   - Destino: **Salvar como PDF**
   - Layout: **Paisagem**
   - Margens: **Nenhuma**
   - Escala: **Padrão** (não usar “ajustar à página”)
   - **Marcar** “Gráficos de plano de fundo”
   - **Desmarcar** “Cabeçalhos e rodapés”
4. Salvar. Conferir que saíram exatamente **10 páginas**.

Pela linha de comando dá no mesmo:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --no-pdf-header-footer --virtual-time-budget=3000 \
  --print-to-pdf="apresentacao.pdf" "file://$PWD/apresentacao.html"
```

> O Safari ignora parte do CSS de impressão e costuma cortar slides. Use Chrome
> ou Edge para gerar o PDF.

---

## Divisão sugerida — todos apresentam

O template exige que todos falem. A divisão abaixo equilibra o tempo e mantém
cada pessoa em um bloco coerente, em vez de picotar por slide.

### Com 4 integrantes (recomendado)

| Quem | Slides | Tempo | Assunto |
|---|---|---|---|
| **1** | 1 · 2 · 3 | 2:30 | Abertura, o problema e os objetivos |
| **2** | 4 · 5 · 6 | 4:10 | Mercado, requisitos, custo e fundamentação |
| **3** | 7 · 8 | 4:10 | A solução e a arquitetura (o diagrama) |
| **4** | demo ao vivo · 9 · 10 | 4:10 | Derrubar o nó, tecnologias e conclusão |

Quem faz a demonstração deve ser quem mais mexeu na infraestrutura — é a parte
com maior chance de imprevisto ao vivo.

### Com 3 integrantes

| Quem | Slides | Tempo |
|---|---|---|
| **1** | 1 · 2 · 3 · 4 | 3:40 |
| **2** | 5 · 6 · 7 | 4:40 |
| **3** | 8 · demo · 9 · 10 | 6:40 |

### Com 5 integrantes — a divisão desta equipe

| Quem | Matrícula | Slides | Tempo | Assunto |
|---|---|---|---|---|
| **Giordana de Farias Franco Bueno Bucci** | 202200513 | 1 · 2 | 1:40 | Abertura e o problema |
| **Carlos Alberto Rodrigues da Silva Junior** | 202200498 | 3 · 4 · 5 | 3:10 | Objetivos, mercado e requisitos |
| **Luiz Felipe Belisário Macedo** | 202200538 | 6 · 7 | 3:30 | Fundamentação científica e solução |
| **Gustavo Henrique Valadares** | 202205539 | 8 | 2:30 | Arquitetura — o diagrama |
| **Gabriel Nery da Silva Espindola** | 202200509 | demo · 9 · 10 | 4:10 | Demo ao vivo, tecnologias e conclusão |

**Por que a demo ficou com o Gabriel:** quem apresenta a parte ao vivo deve ser
quem mais mexeu na infraestrutura. É o bloco com maior chance de imprevisto, e a
diferença entre contornar um erro em dez segundos ou travar está em já ter
digitado aqueles comandos antes.

**Por que o slide 8 tem uma pessoa só:** é o mais longo (2:30) e o mais denso —
o diagrama da arquitetura. Dividir a explicação de um diagrama entre duas
pessoas quebra o raciocínio no meio.

Ajustem conforme quem estiver mais confortável com cada assunto; o que não
convém mudar é quem conduz a demonstração.

**Peso na nota:** Fundamentação vale 15% e Solução vale 15% — os slides 6 e 7,
que ficaram com o Luiz Felipe, somam 30% sozinhos. Vale ensaiar essa parte com
atenção extra.

### Orçamento de tempo (soma = 15:00)

```
S1 0:40 │ S2 1:00 │ S3 0:50 │ S4 1:10 │ S5 1:10
S6 1:50 │ S7 1:40 │ S8 2:30 │ demo 2:00 │ S9 0:50 │ S10 1:20
```

Marcos para conferir no cronômetro do deck: **slide 6 aos 5:00**,
**demo começando aos 10:00**, **slide 10 aos 13:40**.

Regra de sala: quem não está falando não interrompe. Perguntas do professor no
meio da fala são respondidas por quem está com a palavra; se for de outra parte,
responde-se em uma frase e volta-se ao roteiro (“detalho no slide 8”).

---

## A demonstração ao vivo (dentro do slide 8)

Ensaiar na véspera, com o ambiente de pé. Sequência:

1. Projetar `/dashboard` com os 3 nós e duas abas de chat mostrando `node_id`
   diferentes.
2. `make demo-kill` — ou `scripts/kill_node.sh --aws` para derrubar de fato uma
   instância EC2.
3. Narrar os tempos enquanto acontecem: nó some do painel em ≤ 15 s · ALB tira
   do pool em ≤ 45 s · cliente reconecta em < 5 s exibindo outro `node_id`.
4. Continuar enviando mensagens durante a queda e mostrar que **o `seq` não
   regrediu nem pulou**. É este o ponto da demonstração.
5. Mostrar o ASG recriando a instância (< 3 min) — se demorar, deixar rodando e
   voltar a ela no slide 10.

**Plano B, na ordem:** se a rede da sala cair, rodar a mesma demonstração no
cluster local (`make up` && `make demo-kill`); se nem isso, exibir o vídeo de
backup gravado na véspera. Prepare os dois antes.

---

## Perguntas prováveis na arguição

Vale 10% da nota. Respostas curtas, prontas:

**“Por que não usaram Lambda, como sugere a proposta da disciplina?”**
Porque o critério de avaliação pede derrubar uma instância ao vivo, e arquitetura
serverless não tem instância para derrubar. Além disso, Lambda em VPC reintroduz
cold start, e o relógio de Lamport é estado de processo — Lambda não tem processo
durável. Está registrado como ADR-001.

**“Por que Lamport não basta para ordenar as mensagens?”**
Porque ele dá ordem *parcial*: `L(a) < L(b)` não implica que `a` causou `b`.
A interface precisa de uma lista linear. Quem produz a ordem total é o `INCR`
atômico; o relógio vetorial serve para *detectar* a concorrência que o escalar
esconde.

**“O sequenciador não é um gargalo?”**
É um ponto de serialização **por sala**, assumido conscientemente. O teto é o
teto do `INCR` do Redis, ordens de magnitude acima de qualquer sala real, e as
salas são independentes — o sistema continua escalando na dimensão que de fato
cresce em um chat, que é o número de salas.

**“E se o Redis cair?”**
O chat para. É deliberado: sem o Redis não há nem ordenação nem difusão, então
preferimos falha coerente a degradação silenciosa com ordem errada. É a
limitação mais séria do projeto e está no slide 10; a mitigação de produção é
ElastiCache Multi-AZ com failover, fora do Free Tier.

**“Redis Pub/Sub não perde mensagem?”**
Perde — é at-most-once. A durabilidade vem da camada de persistência, não do
canal: o cliente detecta a lacuna na fila de hold-back, pede `resync` e recebe
o backlog do DynamoDB. Trocamos um canal durável e lento por um canal rápido
mais armazenamento durável.

**“Por que EC2 em subrede pública? Isso não é inseguro?”**
É um desvio consciente de boa prática, por custo: NAT Gateway custa ~US$ 32/mês
por AZ e não é Free Tier. A proteção efetiva vem dos Security Groups encadeados
— a porta 8000 só aceita tráfego vindo do Security Group do balanceador, então a
aplicação não é alcançável da internet mesmo com IP público. ADR-006 descreve a
configuração de produção.

**“Vocês testaram com quantos usuários? E a latência com 1.200?”**
1.200 conexões simultâneas, todas estabelecidas, zero falha. Nessa marca o
gargalo medido foi a máquina que hospedava ao mesmo tempo os 3 nós e os 1.200
clientes — não a arquitetura. Na carga realista de 300 clientes em 30 salas o
p95 ficou em 37 ms, contra a meta de 200 ms.

**“Como vocês provam que a ordem é a mesma para todos?”**
O gerador de carga reproduz a fila de hold-back de cada cliente e compara, sala
a sala, a subsequência observada por todos eles dentro da janela comum,
exigindo contiguidade, unicidade e identidade. Roda no CI e falha com código 1
se qualquer sala divergir.

---

## Conferência final, na véspera

- [ ] Os três `{{...}}` substituídos nos dois arquivos (`grep -c "{{"` → 0)
- [ ] Deck abre em tela cheia no notebook **que vai ser usado** na sala
- [ ] PDF exportado com 10 páginas, salvo como reserva em pendrive
- [ ] Ambiente AWS de pé e `/dashboard` respondendo
- [ ] `make up` funcionando como plano B local
- [ ] Vídeo de backup da demo de falha gravado
- [ ] Cronômetro ensaiado ao menos uma vez de ponta a ponta
- [ ] **Depois da apresentação:** `terraform destroy` (o ambiente custa ~US$ 0,07/h)
