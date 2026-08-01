#!/usr/bin/env bash
# =============================================================================
# deploy.sh — publica uma nova versão do SalaViva na AWS, sem indisponibilidade.
#
# Sequência:
#   1. Verificações prévias (credenciais, ferramentas, infraestrutura existente)
#   2. Build e push da imagem para o ECR  (delegado a build_push.sh)
#   3. Instance refresh do Auto Scaling Group — troca as instâncias em ondas,
#      mantendo capacidade mínima saudável, e é a mesma mecânica que reage à
#      falha demonstrada em EC3: o ASG substitui instância por instância.
#   4. Verificação: /readyz pelo ALB e contagem de nós em /api/nodes
#
# Por que instance refresh e não recriar o ASG: o refresh respeita o health
# check do target group e só encerra a instância antiga depois que a nova está
# InService. Um `terraform taint` derrubaria tudo de uma vez e a demo cairia.
#
# Pré-requisito: 'make tf-apply' já executado ao menos uma vez.
#
# Uso:
#   ./scripts/deploy.sh
#   ./scripts/deploy.sh --sem-build         # só faz o refresh do ASG
#   ./scripts/deploy.sh --asg salaviva-asg --regiao us-east-1
#   ./scripts/deploy.sh --sim               # sem confirmação interativa
# =============================================================================

set -euo pipefail

if [[ -t 1 ]]; then
  VERM=$'\033[31m'; VERD=$'\033[32m'; AMAR=$'\033[33m'
  AZUL=$'\033[36m'; NEG=$'\033[1m'; CINZ=$'\033[90m'; FIM=$'\033[0m'
else
  VERM=''; VERD=''; AMAR=''; AZUL=''; NEG=''; CINZ=''; FIM=''
fi
info()  { printf '%s\n' "${AZUL}▸${FIM} $*"; }
ok()    { printf '%s\n' "${VERD}✓${FIM} $*"; }
aviso() { printf '%s\n' "${AMAR}!${FIM} $*"; }
morrer(){ printf '%s\n' "${VERM}✗${FIM} $*" >&2; exit 1; }
etapa() { printf '\n%s\n\n' "${NEG}${AZUL}── $* ─────────────────────────────────${FIM}"; }

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${RAIZ}/infra/terraform"

REGIAO="${AWS_REGION:-us-east-1}"
ASG_NOME="${SALAVIVA_ASG_NAME:-}"
URL_BASE="${SALAVIVA_LB_URL:-}"
FAZER_BUILD=1
SEM_PERGUNTA=0
LIMITE=900          # o refresh de 3 t3.micro costuma levar 4-8 min
TAG_ARGS=()

uso() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sem-build|--skip-build) FAZER_BUILD=0 ;;
    --asg)             ASG_NOME="${2:?--asg exige um valor}"; shift ;;
    --url)             URL_BASE="${2:?--url exige um valor}"; shift ;;
    --regiao|--region) REGIAO="${2:?--regiao exige um valor}"; shift ;;
    --tag)             TAG_ARGS=(--tag "${2:?--tag exige um valor}"); shift ;;
    --limite|--timeout) LIMITE="${2:?--limite exige segundos}"; shift ;;
    --sim|--yes|-y)    SEM_PERGUNTA=1 ;;
    -h|--help)         uso ;;
    *) morrer "opção desconhecida: $1 (use --help)" ;;
  esac
  shift
done

confirmar() {
  [[ "$SEM_PERGUNTA" -eq 1 ]] && return 0
  local r; printf '%s' "${AMAR}?${FIM} $1 [s/N] "; read -r r || true
  [[ "$r" =~ ^([sS]|[sS][iI][mM]|[yY])$ ]]
}

# =============================================================================
etapa "1/4 · Verificações prévias"
# =============================================================================
for cmd in aws docker curl python3; do
  command -v "$cmd" >/dev/null 2>&1 || morrer "comando obrigatório ausente: $cmd"
done
aws sts get-caller-identity \
  --query 'Account' --output text >/dev/null 2>&1 \
  || morrer "credenciais AWS não configuradas (rode 'aws configure')."

CONTA="$(aws sts get-caller-identity --query 'Account' --output text)"
ok "Conta AWS ${CONTA}, região ${REGIAO}."

# --- Auto Scaling Group -------------------------------------------------------
if [[ -z "$ASG_NOME" ]] && [[ -d "${TF_DIR}/.terraform" ]] && command -v terraform >/dev/null 2>&1; then
  for nome in asg_name autoscaling_group_name asg_nome; do
    ASG_NOME="$(terraform -chdir="$TF_DIR" output -raw "$nome" 2>/dev/null || true)"
    [[ -n "$ASG_NOME" ]] && break
  done
fi
if [[ -z "$ASG_NOME" ]]; then
  ASG_NOME="$(aws autoscaling describe-auto-scaling-groups --region "$REGIAO" \
    --query "AutoScalingGroups[?contains(AutoScalingGroupName, 'salaviva')].AutoScalingGroupName | [0]" \
    --output text 2>/dev/null || true)"
  [[ "$ASG_NOME" == "None" ]] && ASG_NOME=""
fi
[[ -n "$ASG_NOME" ]] || morrer "ASG não encontrado. Rode 'make tf-apply' primeiro ou informe --asg."
ok "Auto Scaling Group: ${NEG}${ASG_NOME}${FIM}"

# --- URL do balanceador -------------------------------------------------------
if [[ -z "$URL_BASE" ]] && [[ -d "${TF_DIR}/.terraform" ]] && command -v terraform >/dev/null 2>&1; then
  for nome in alb_url app_url alb_dns_name; do
    valor="$(terraform -chdir="$TF_DIR" output -raw "$nome" 2>/dev/null || true)"
    if [[ -n "$valor" ]]; then
      [[ "$valor" == http* ]] && URL_BASE="$valor" || URL_BASE="http://${valor}"
      break
    fi
  done
fi
if [[ -z "$URL_BASE" ]]; then
  DNS="$(aws elbv2 describe-load-balancers --region "$REGIAO" \
    --query "LoadBalancers[?contains(LoadBalancerName, 'salaviva')].DNSName | [0]" \
    --output text 2>/dev/null || true)"
  [[ -n "$DNS" && "$DNS" != "None" ]] && URL_BASE="http://${DNS}"
fi
[[ -n "$URL_BASE" ]] || morrer "URL do ALB não descoberta. Informe com --url."
URL_BASE="${URL_BASE%/}"
ok "Balanceador: ${URL_BASE}"

CAPACIDADE="$(aws autoscaling describe-auto-scaling-groups --region "$REGIAO" \
  --auto-scaling-group-names "$ASG_NOME" \
  --query 'AutoScalingGroups[0].DesiredCapacity' --output text)"
ok "Capacidade desejada atual: ${CAPACIDADE} instâncias."

confirmar "Publicar nova versão em '${ASG_NOME}'?" || { aviso "Cancelado."; exit 0; }

# =============================================================================
etapa "2/4 · Imagem"
# =============================================================================
if [[ "$FAZER_BUILD" -eq 1 ]]; then
  "${RAIZ}/scripts/build_push.sh" --regiao "$REGIAO" "${TAG_ARGS[@]}"
else
  aviso "Build ignorado (--sem-build): o refresh usará a tag 'latest' já publicada."
fi

# =============================================================================
etapa "3/4 · Instance refresh do Auto Scaling Group"
# =============================================================================
# MinHealthyPercentage=50 com 3 instâncias mantém pelo menos 2 servindo durante
# toda a troca — o serviço nunca fica indisponível. InstanceWarmup=90 dá tempo
# de o container baixar a imagem e o /readyz aprovar antes de a instância contar
# como saudável; sem isso o ASG julgaria cedo demais e entraria em laço de
# substituição.
ID_REFRESH="$(aws autoscaling start-instance-refresh \
  --region "$REGIAO" \
  --auto-scaling-group-name "$ASG_NOME" \
  --preferences '{"MinHealthyPercentage":50,"InstanceWarmup":90,"ScaleInProtectedInstances":"Ignore"}' \
  --query 'InstanceRefreshId' --output text)"
ok "Refresh iniciado: ${ID_REFRESH}"
info "Acompanhando (as instâncias são trocadas em ondas)..."

INICIO="$(date +%s)"
while :; do
  DECORRIDO=$(( $(date +%s) - INICIO ))
  LINHA="$(aws autoscaling describe-instance-refreshes --region "$REGIAO" \
    --auto-scaling-group-name "$ASG_NOME" --instance-refresh-ids "$ID_REFRESH" \
    --query 'InstanceRefreshes[0].[Status,PercentageComplete]' --output text)"
  ESTADO="$(awk '{print $1}' <<<"$LINHA")"
  PCT="$(awk '{print $2}' <<<"$LINHA")"
  [[ "$PCT" == "None" || -z "$PCT" ]] && PCT=0

  printf '\r  %s t+%-4ss  estado=%-12s progresso=%s%%   ' "${CINZ}·${FIM}" "$DECORRIDO" "$ESTADO" "$PCT"

  case "$ESTADO" in
    Successful) printf '\n'; ok "Refresh concluído em ${DECORRIDO}s."; break ;;
    Failed|Cancelled)
      printf '\n'
      aws autoscaling describe-instance-refreshes --region "$REGIAO" \
        --auto-scaling-group-name "$ASG_NOME" --instance-refresh-ids "$ID_REFRESH" \
        --query 'InstanceRefreshes[0].StatusReason' --output text
      morrer "Refresh terminou com estado '${ESTADO}'." ;;
  esac

  if (( DECORRIDO > LIMITE )); then
    printf '\n'
    morrer "Tempo limite de ${LIMITE}s excedido. Verifique o console do ASG (refresh ${ID_REFRESH})."
  fi
  sleep 10
done

# =============================================================================
etapa "4/4 · Verificação"
# =============================================================================
info "Checando ${URL_BASE}/readyz ..."
for tentativa in $(seq 1 30); do
  if curl -fsS --max-time 5 "${URL_BASE}/readyz" >/dev/null 2>&1; then
    ok "O balanceador responde /readyz com sucesso."
    break
  fi
  printf '\r  %s tentativa %s/30...   ' "${CINZ}·${FIM}" "$tentativa"
  sleep 5
  [[ "$tentativa" -eq 30 ]] && { printf '\n'; morrer "o ALB não aprovou /readyz."; }
done

SALAVIVA_NOS_JSON="$(curl -fsS --max-time 5 "${URL_BASE}/api/nodes" 2>/dev/null || echo '{}')"
export SALAVIVA_NOS_JSON
printf '\n'
python3 <<'PY'
import json
import os

try:
    dados = json.loads(os.environ.get("SALAVIVA_NOS_JSON", "{}"))
except json.JSONDecodeError:
    dados = {}

nos = dados.get("nodes", [])
print("  Nós registrados: {}".format(len(nos)))
for no in sorted(nos, key=lambda n: n.get("node_id", "")):
    print("    - {:<22} conexoes={:<4} uptime={:.0f}s".format(
        no.get("node_id", "?"),
        no.get("connections", 0),
        float(no.get("uptime_seconds", 0)),
    ))
PY

printf '\n'
ok "${NEG}Deploy concluído.${FIM}"
printf '%s\n' "  Chat ........... ${URL_BASE}/"
printf '%s\n' "  Painel de nós .. ${URL_BASE}/dashboard"
printf '%s\n' "  Métricas ....... ${URL_BASE}/metrics"
printf '\n'
printf '%s\n' "${CINZ}  Demonstração de falha:  ./scripts/kill_node.sh --aws --url ${URL_BASE}${FIM}"
printf '%s\n' "${CINZ}  Painel ao vivo:         ./scripts/watch_cluster.sh --url ${URL_BASE}${FIM}"
printf '%s\n' "${CINZ}  Ao terminar (custo!):   make teardown${FIM}"
printf '\n'
