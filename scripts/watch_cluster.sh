#!/usr/bin/env bash
# =============================================================================
# watch_cluster.sh — painel de terminal do cluster SalaViva.
#
# Propósito: dar, em um único terminal projetado ao lado do chat, a evidência
# de que o sistema é de fato distribuído — três nós independentes, cada um com
# suas próprias conexões e seu próprio relógio de Lamport, todos convergindo
# para a mesma ordem de mensagens.
#
# Durante a demonstração de falha (critério EC3) este painel é o instrumento de
# medida: o nó derrubado some da lista, as conexões migram para os
# sobreviventes e, em seguida, o substituto aparece — tudo ao vivo, sem
# ninguém precisar rodar comando nenhum.
#
# Fontes de dados (o próprio sistema, sem instrumentação externa):
#   GET /api/nodes  — nós vivos segundo o registro ZSET `chat:nodes` no Redis
#   GET /api/rooms  — salas com presença ativa e último `seq`
#
# Uso:
#   ./scripts/watch_cluster.sh                       # via balanceador, a cada 2s
#   ./scripts/watch_cluster.sh --intervalo 1
#   ./scripts/watch_cluster.sh --url http://meu-alb.us-east-1.elb.amazonaws.com
#   ./scripts/watch_cluster.sh --uma-vez             # imprime uma vez e sai
#
# Encerre com Ctrl+C.
# =============================================================================

set -euo pipefail

URL_BASE="${SALAVIVA_LB_URL:-http://localhost:8080}"
INTERVALO=2
UMA_VEZ=0

uso() {
  sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)                  URL_BASE="${2:?--url exige um valor}"; shift ;;
    --intervalo|--interval) INTERVALO="${2:?--intervalo exige segundos}"; shift ;;
    --uma-vez|--once)       UMA_VEZ=1 ;;
    -h|--help)              uso ;;
    *) printf 'opção desconhecida: %s (use --help)\n' "$1" >&2; exit 1 ;;
  esac
  shift
done

URL_BASE="${URL_BASE%/}"

command -v curl    >/dev/null 2>&1 || { echo "comando obrigatório ausente: curl" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "comando obrigatório ausente: python3" >&2; exit 1; }

# Restaura o cursor ao sair (o painel o esconde para evitar tremulação).
restaurar() { printf '\033[?25h\n'; }
trap restaurar EXIT INT TERM

# -----------------------------------------------------------------------------
# Renderizador. O bash busca os JSON; o Python formata. Fazer a tabela em awk
# exigiria um parser de JSON improvisado — e o projeto já depende de Python.
# -----------------------------------------------------------------------------
renderizar() {
  local nos_json rooms_json
  nos_json="$(curl -fsS --max-time 3 "${URL_BASE}/api/nodes" 2>/dev/null || true)"
  rooms_json="$(curl -fsS --max-time 3 "${URL_BASE}/api/rooms" 2>/dev/null || true)"

  SALAVIVA_URL="$URL_BASE" \
  SALAVIVA_NOS_JSON="$nos_json" \
  SALAVIVA_ROOMS_JSON="$rooms_json" \
  SALAVIVA_INTERVALO="$INTERVALO" \
  python3 <<'PY'
import json
import os
from datetime import datetime, timezone

AZUL, VERD, AMAR, VERM = "\033[36m", "\033[32m", "\033[33m", "\033[31m"
NEG, CINZ, FIM = "\033[1m", "\033[90m", "\033[0m"
LARG = 78


def carregar(nome):
    bruto = os.environ.get(nome, "")
    if not bruto:
        return None
    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        return None


url = os.environ.get("SALAVIVA_URL", "")
intervalo = os.environ.get("SALAVIVA_INTERVALO", "2")
nos_dados = carregar("SALAVIVA_NOS_JSON")
salas_dados = carregar("SALAVIVA_ROOMS_JSON")
agora = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")

print(f"{NEG}{AZUL}╔{'═' * LARG}╗{FIM}")
titulo = "SalaViva · painel do cluster"
print(f"{NEG}{AZUL}║{FIM} {NEG}{titulo}{FIM}{' ' * (LARG - len(titulo) - 1)}{NEG}{AZUL}║{FIM}")
rodape = f"{url}   ·   atualiza a cada {intervalo}s   ·   {agora}"
print(f"{NEG}{AZUL}║{FIM} {CINZ}{rodape}{FIM}{' ' * (LARG - len(rodape) - 1)}{NEG}{AZUL}║{FIM}")
print(f"{NEG}{AZUL}╚{'═' * LARG}╝{FIM}")
print()

# --- Nós vivos ---------------------------------------------------------------
if nos_dados is None:
    print(f"  {VERM}Sem resposta de {url}/api/nodes{FIM}")
    print(f"  {CINZ}O cluster está no ar? Tente 'make up' ou confira a URL com --url.{FIM}")
    raise SystemExit(0)

nos = sorted(nos_dados.get("nodes", []), key=lambda n: n.get("node_id", ""))
eu = nos_dados.get("self", "?")

print(f"  {NEG}NÓS VIVOS ({len(nos)}){FIM}   {CINZ}respondeu: {eu}{FIM}")
print(f"  {CINZ}{'─' * LARG}{FIM}")
cab = f"  {'NÓ':<20}{'CONEXÕES':>9}{'SALAS':>7}{'PUBLIC.':>9}{'ENTREG.':>9}{'LAMPORT':>9}{'UPTIME':>9}"
print(f"{NEG}{cab}{FIM}")

tot_conexoes = tot_salas = tot_pub = tot_ent = 0
for no in nos:
    conexoes = int(no.get("connections", 0))
    salas = int(no.get("rooms", 0))
    publicadas = int(no.get("messages_published", 0))
    entregues = int(no.get("messages_delivered", 0))
    lamport = int(no.get("lamport", 0))
    uptime = float(no.get("uptime_seconds", 0))

    tot_conexoes += conexoes
    tot_salas = max(tot_salas, salas)
    tot_pub += publicadas
    tot_ent += entregues

    # Nó recém-nascido (menos de 60 s) recebe destaque: durante a demo de falha
    # é assim que o substituto criado pelo Auto Scaling se identifica sozinho.
    if uptime < 60:
        cor, marca = VERD, " ←novo"
    elif conexoes == 0:
        cor, marca = AMAR, ""
    else:
        cor, marca = "", ""

    rotulo = no.get("node_id", "?")[:19] + marca
    print(
        f"  {cor}{rotulo:<20}{conexoes:>9}{salas:>7}{publicadas:>9}"
        f"{entregues:>9}{lamport:>9}{uptime:>8.0f}s{FIM}"
    )

if not nos:
    print(f"  {VERM}(nenhum nó registrado — o Redis está acessível?){FIM}")

print(f"  {CINZ}{'─' * LARG}{FIM}")
print(
    f"  {NEG}{'TOTAL':<20}{tot_conexoes:>9}{tot_salas:>7}{tot_pub:>9}{tot_ent:>9}{FIM}"
)
print()

# --- Salas -------------------------------------------------------------------
print(f"  {NEG}SALAS ATIVAS{FIM}")
print(f"  {CINZ}{'─' * LARG}{FIM}")
if salas_dados is None:
    print(f"  {CINZ}(sem dados de /api/rooms){FIM}")
else:
    salas = salas_dados.get("rooms", [])
    if not salas:
        print(f"  {CINZ}(nenhuma sala com presença ativa){FIM}")
    else:
        print(f"{NEG}  {'SALA':<28}{'MEMBROS':>9}{'ÚLTIMO SEQ':>13}{FIM}")
        for sala in salas:
            print(
                f"  {str(sala.get('room_id', '?'))[:27]:<28}"
                f"{int(sala.get('member_count', 0)):>9}"
                f"{int(sala.get('last_seq', 0)):>13}"
            )
print()

# --- Leitura do painel -------------------------------------------------------
print(f"  {CINZ}Como ler este painel:{FIM}")
print(f"  {CINZ}  · LAMPORT igual entre nós = os relógios lógicos convergiram"
      f" (regra max(L, L_msg)+1).{FIM}")
print(f"  {CINZ}  · PUBLIC. distribuído entre nós = mensagens nascendo em processos"
      f" diferentes.{FIM}")
print(f"  {CINZ}  · ÚLTIMO SEQ nunca regride: o contador vive no Redis, não no nó.{FIM}")
print(f"  {CINZ}  · Um nó sumindo daqui é a falha; um nó verde '←novo' é a recuperação.{FIM}")
print()
print(f"  {CINZ}Ctrl+C para sair.{FIM}")
PY
}

if [[ "$UMA_VEZ" -eq 1 ]]; then
  renderizar
  exit 0
fi

# Loop próprio em vez de `watch`: o `watch` do BSD (macOS) não vem instalado e o
# do GNU trata cores de forma inconsistente entre versões. Um clear + render é
# portátil e mantém o ANSI intacto.
printf '\033[?25l'   # esconde o cursor
while :; do
  saida="$(renderizar)"
  clear
  printf '%s\n' "$saida"
  sleep "$INTERVALO"
done
