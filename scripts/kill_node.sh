#!/usr/bin/env bash
# =============================================================================
# kill_node.sh — simulação de falha de nó, cronometrada.
#
# É o script executado ao vivo na apresentação. O critério de avaliação EC3 diz
# que "o professor pode solicitar a simulação de uma falha (ex: derrubar uma
# instância EC2) para verificar a tolerância a falhas". Este script faz isso e,
# mais importante, MEDE e IMPRIME o que aconteceu, em vez de deixar a
# tolerância a falhas como afirmação.
#
# Dois modos:
#
#   local (padrão)  Derruba um container do cluster Docker Compose com
#                   `docker kill`. A política `restart: unless-stopped` do
#                   compose recria o container — é o análogo local do Auto
#                   Scaling Group recriando a instância.
#
#   --aws           Consulta o Auto Scaling Group com
#                   `aws autoscaling describe-auto-scaling-groups`, escolhe UMA
#                   instância InService e a encerra com
#                   `aws ec2 terminate-instances`. O ASG detecta a perda de
#                   capacidade e provisiona uma substituta.
#
# Em ambos os modos o script cronometra três marcos e os imprime ao final:
#
#   T1  detecção  — instante em que o nó derrubado some de GET /api/nodes
#   T2  reposição — instante em que um nó substituto aparece em /api/nodes
#   T3  quórum    — instante em que o cluster volta ao número original de nós
#
# Enquanto isso, mantenha `./scripts/watch_cluster.sh` em outro terminal e a
# página /dashboard aberta no projetor.
#
# Exemplos:
#   ./scripts/kill_node.sh                      # derruba node-b no Compose
#   ./scripts/kill_node.sh node-c               # derruba um nó específico
#   ./scripts/kill_node.sh --segurar 25         # mantém o nó fora por 25 s
#   ./scripts/kill_node.sh --aws                # derruba uma EC2 do ASG
#   ./scripts/kill_node.sh --aws --url http://meu-alb-123.us-east-1.elb.amazonaws.com
# =============================================================================

set -euo pipefail

# --- Cores -------------------------------------------------------------------
if [[ -t 1 ]]; then
  VERM=$'\033[31m'; VERD=$'\033[32m'; AMAR=$'\033[33m'
  AZUL=$'\033[36m'; NEG=$'\033[1m';   CINZ=$'\033[90m'; FIM=$'\033[0m'
else
  VERM=''; VERD=''; AMAR=''; AZUL=''; NEG=''; CINZ=''; FIM=''
fi

info()  { printf '%s\n' "${AZUL}▸${FIM} $*"; }
ok()    { printf '%s\n' "${VERD}✓${FIM} $*"; }
aviso() { printf '%s\n' "${AMAR}!${FIM} $*"; }
erro()  { printf '%s\n' "${VERM}✗${FIM} $*" >&2; }
titulo() {
  printf '\n%s\n' "${NEG}${AZUL}══════════════════════════════════════════════════════════════${FIM}"
  printf '%s\n'   "${NEG}  $*${FIM}"
  printf '%s\n\n' "${NEG}${AZUL}══════════════════════════════════════════════════════════════${FIM}"
}
morrer() { erro "$*"; exit 1; }

# --- Parâmetros --------------------------------------------------------------
MODO="local"
ALVO=""
URL_BASE="${SALAVIVA_LB_URL:-http://localhost:8080}"
ASG_NOME="${SALAVIVA_ASG_NAME:-}"
REGIAO="${AWS_REGION:-us-east-1}"
SEGURAR=0                       # segundos mantendo o nó fora do ar (modo local)
LIMITE=300                      # tempo máximo de espera pela recuperação, em s
INTERVALO=2                     # período de amostragem, em segundos
SEM_PERGUNTA=0

uso() {
  sed -n '2,39p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --aws)              MODO="aws" ;;
    --url)              URL_BASE="${2:?--url exige um valor}"; shift ;;
    --asg)              ASG_NOME="${2:?--asg exige um valor}"; shift ;;
    --regiao|--region)  REGIAO="${2:?--regiao exige um valor}"; shift ;;
    --segurar|--hold)   SEGURAR="${2:?--segurar exige um valor em segundos}"; shift ;;
    --limite|--timeout) LIMITE="${2:?--limite exige um valor em segundos}"; shift ;;
    --sim|--yes|-y)     SEM_PERGUNTA=1 ;;
    -h|--help)          uso ;;
    -*)                 morrer "opção desconhecida: $1  (use --help)" ;;
    *)                  ALVO="$1" ;;
  esac
  shift
done

URL_BASE="${URL_BASE%/}"

# --- Utilitários -------------------------------------------------------------

precisa() { command -v "$1" >/dev/null 2>&1 || morrer "comando obrigatório ausente: $1"; }

agora() { date +%s; }

# Lê GET /api/nodes e devolve os node_id, um por linha. Silencioso em caso de
# falha: durante a janela de indisponibilidade é normal a consulta falhar, e
# isso não deve abortar o script (que é justamente quem mede essa janela).
consultar_nos() {
  local url="$1"
  curl -fsS --max-time 4 "${url}/api/nodes" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    dados = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for no in dados.get("nodes", []):
    print(no.get("node_id", "?"))' 2>/dev/null || true
}

# Snapshot legível do cluster: node_id, conexões e mensagens publicadas.
tabela_nos() {
  local url="$1"
  curl -fsS --max-time 4 "${url}/api/nodes" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    dados = json.load(sys.stdin)
except Exception:
    print("    (nenhuma resposta do cluster)")
    sys.exit(0)
nos = dados.get("nodes", [])
if not nos:
    print("    (nenhum nó registrado)")
for no in sorted(nos, key=lambda n: n.get("node_id", "")):
    print("    {:<24} conexoes={:<4} publicadas={:<6} lamport={:<6} uptime={:.0f}s".format(
        no.get("node_id", "?"),
        no.get("connections", 0),
        no.get("messages_published", 0),
        no.get("lamport", 0),
        float(no.get("uptime_seconds", 0)),
    ))' 2>/dev/null || printf '    (nenhuma resposta do cluster)\n'
}

confirmar() {
  [[ "$SEM_PERGUNTA" -eq 1 ]] && return 0
  local resposta
  printf '%s' "${AMAR}?${FIM} $1 [s/N] "
  read -r resposta || true
  [[ "$resposta" =~ ^([sS]|[sS][iI][mM]|[yY])$ ]]
}

# --- Relatório final ---------------------------------------------------------
T_DETECCAO=""
T_REPOSICAO=""
T_QUORUM=""

relatorio() {
  local derrubado="$1" antes="$2" depois="$3"
  titulo "Resultado da simulação de falha"
  printf '  %-42s %s\n' "Nó derrubado"                 "${NEG}${derrubado}${FIM}"
  printf '  %-42s %s\n' "Nós antes da falha"           "${antes}"
  printf '  %-42s %s\n' "Nós ao final"                 "${depois}"
  printf '  %-42s %s\n' "T1 · nó sumiu de /api/nodes"  "${T_DETECCAO:-não observado}"
  printf '  %-42s %s\n' "T2 · substituto apareceu"     "${T_REPOSICAO:-não observado}"
  printf '  %-42s %s\n' "T3 · capacidade restabelecida" "${T_QUORUM:-não observado}"
  printf '\n'
  printf '%s\n' "${CINZ}  O que verificar agora, com o professor olhando:${FIM}"
  printf '%s\n' "${CINZ}    1. Os clientes do nó derrubado reconectaram em OUTRO node_id${FIM}"
  printf '%s\n' "${CINZ}       (o cabeçalho do chat mostra o nó atual).${FIM}"
  printf '%s\n' "${CINZ}    2. Nenhuma mensagem se perdeu — a sequência continua contígua:${FIM}"
  printf '%s\n' "${CINZ}       curl -s ${URL_BASE}/api/rooms/geral/messages | grep contiguous${FIM}"
  printf '%s\n' "${CINZ}    3. O seq NÃO regrediu: ele vive no Redis, não no nó que morreu.${FIM}"
  printf '\n'
}

# =============================================================================
# MODO LOCAL — Docker Compose
# =============================================================================
modo_local() {
  precisa docker
  precisa curl
  precisa python3
  docker info >/dev/null 2>&1 \
    || morrer "o daemon do Docker não está rodando (abra o Docker Desktop e tente de novo)."

  # Containers de aplicação vivos, pelos nomes fixos definidos no compose.
  local vivos
  vivos="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^node-[a-z]+$' | sort || true)"
  [[ -n "$vivos" ]] || morrer "nenhum container node-* em execução. Rode 'make up' antes."

  # Escolha do alvo: o informado, ou node-b (o do meio, mais didático), ou o
  # primeiro disponível.
  if [[ -z "$ALVO" ]]; then
    if grep -qx 'node-b' <<<"$vivos"; then ALVO="node-b"; else ALVO="$(head -n1 <<<"$vivos")"; fi
  fi
  grep -qx "$ALVO" <<<"$vivos" || morrer "container '$ALVO' não está em execução. Ativos: $(tr '\n' ' ' <<<"$vivos")"

  # Observador: um nó SOBREVIVENTE, consultado direto na sua porta. Consultar
  # pelo balanceador seria pedir para a própria camada sob teste responder
  # durante a falha — a medição ficaria contaminada pelo failover do nginx.
  local sobrevivente porta_obs url_obs
  sobrevivente="$(grep -vx "$ALVO" <<<"$vivos" | head -n1 || true)"
  [[ -n "$sobrevivente" ]] || morrer "só há um nó vivo; derrubá-lo não demonstra tolerância a falhas."
  case "$sobrevivente" in
    node-a) porta_obs=8001 ;;
    node-b) porta_obs=8002 ;;
    node-c) porta_obs=8003 ;;
    *)      porta_obs="" ;;
  esac
  if [[ -n "$porta_obs" ]]; then url_obs="http://localhost:${porta_obs}"; else url_obs="$URL_BASE"; fi

  titulo "Simulação de falha — cluster local (Docker Compose)"
  info "Alvo da falha ......... ${NEG}${ALVO}${FIM}"
  info "Observando por ........ ${sobrevivente} (${url_obs}/api/nodes)"
  info "Balanceador ........... ${URL_BASE}"
  printf '\n'

  local nos_antes total_antes
  nos_antes="$(consultar_nos "$url_obs")"
  total_antes="$(grep -c . <<<"$nos_antes" || true)"
  printf '%s\n' "${NEG}  Cluster ANTES da falha (${total_antes} nós):${FIM}"
  tabela_nos "$url_obs"
  printf '\n'

  confirmar "Derrubar '${ALVO}' agora?" || { aviso "Cancelado."; exit 0; }

  local t0; t0="$(agora)"

  if [[ "$SEGURAR" -gt 0 ]]; then
    # Variante didática: `docker stop --time 0` envia SIGKILL e, por ser uma
    # parada solicitada pelo usuário, NÃO dispara a política de restart. Isso
    # mantém o cluster visivelmente reduzido pelo tempo pedido, para que a
    # plateia veja o painel encolher antes da recuperação.
    info "Encerrando '${ALVO}' (SIGKILL) e mantendo-o fora por ${SEGURAR}s..."
    docker stop --time 0 "$ALVO" >/dev/null
  else
    info "Encerrando '${ALVO}' com SIGKILL (docker kill)..."
    docker kill "$ALVO" >/dev/null
  fi
  ok "$(printf 'Nó %s derrubado em t+0s.' "${NEG}${ALVO}${FIM}")"
  printf '\n'

  # --- Fase 1: detecção -------------------------------------------------------
  info "Aguardando o cluster perceber a ausência (registro em Redis, TTL de heartbeat)..."
  local decorrido restantes
  while :; do
    decorrido=$(( $(agora) - t0 ))
    restantes="$(consultar_nos "$url_obs")"
    if ! grep -qx "$ALVO" <<<"$restantes"; then
      T_DETECCAO="t+${decorrido}s"
      ok "$(printf "'%s' sumiu de /api/nodes em ${NEG}t+%ss${FIM}. Nós restantes: %s" \
            "$ALVO" "$decorrido" "$(tr '\n' ' ' <<<"$restantes")")"
      break
    fi
    printf '\r  %s t+%-4ss ainda listado (o heartbeat expira em até 15 s)...   ' "${CINZ}·${FIM}" "$decorrido"
    if (( decorrido > LIMITE )); then
      printf '\n'; aviso "O nó continuou listado após ${LIMITE}s. Prosseguindo mesmo assim."
      break
    fi
    sleep "$INTERVALO"
  done
  printf '\n'

  # --- Fase 2: reposição ------------------------------------------------------
  if [[ "$SEGURAR" -gt 0 ]]; then
    info "Mantendo o nó fora do ar por ${SEGURAR}s — mostre o /dashboard com ${total_antes} menos um nó."
    local fim_espera=$(( $(agora) + SEGURAR ))
    while (( $(agora) < fim_espera )); do
      printf '\r  %s faltam %-3ss para religar '\''%s'\''...   ' "${CINZ}·${FIM}" "$(( fim_espera - $(agora) ))" "$ALVO"
      sleep 1
    done
    printf '\n'
    info "Religando '${ALVO}' (equivalente ao ASG provisionando a substituta)..."
    docker start "$ALVO" >/dev/null
  else
    info "A política 'restart: unless-stopped' deve recriar o container — é o papel do ASG na nuvem."
  fi

  local recuperado=0
  while :; do
    decorrido=$(( $(agora) - t0 ))
    restantes="$(consultar_nos "$url_obs")"
    local total_agora; total_agora="$(grep -c . <<<"$restantes" || true)"

    if grep -qx "$ALVO" <<<"$restantes"; then
      [[ -z "$T_REPOSICAO" ]] && T_REPOSICAO="t+${decorrido}s"
      if (( total_agora >= total_antes )); then
        T_QUORUM="t+${decorrido}s"
        printf '\n'
        ok "$(printf "Capacidade restabelecida em ${NEG}t+%ss${FIM} com %s nós." "$decorrido" "$total_agora")"
        recuperado=1
        break
      fi
    fi

    printf '\r  %s t+%-4ss aguardando o retorno de '\''%s'\'' (%s/%s nós)...   ' \
      "${CINZ}·${FIM}" "$decorrido" "$ALVO" "$total_agora" "$total_antes"

    # Rede de segurança: se após 30 s o container não voltou sozinho (política
    # de restart desabilitada, por exemplo), religamos explicitamente. A demo
    # não pode depender de um detalhe de configuração do Docker.
    if (( decorrido > 30 )) && [[ "$(docker inspect -f '{{.State.Running}}' "$ALVO" 2>/dev/null)" != "true" ]]; then
      printf '\n'
      aviso "O container não voltou sozinho; religando explicitamente."
      docker start "$ALVO" >/dev/null || true
    fi

    if (( decorrido > LIMITE )); then
      printf '\n'; aviso "Tempo limite de ${LIMITE}s atingido sem recuperação completa."
      break
    fi
    sleep "$INTERVALO"
  done

  printf '\n%s\n' "${NEG}  Cluster DEPOIS da recuperação:${FIM}"
  tabela_nos "$url_obs"

  local nos_depois total_depois
  nos_depois="$(consultar_nos "$url_obs")"
  total_depois="$(grep -c . <<<"$nos_depois" || true)"
  relatorio "$ALVO" "$total_antes" "$total_depois"
  [[ "$recuperado" -eq 1 ]] || aviso "Verifique 'docker compose ps' — a recuperação não foi confirmada."
}

# =============================================================================
# MODO AWS — Auto Scaling Group
# =============================================================================
modo_aws() {
  precisa aws
  precisa curl
  precisa python3

  aws sts get-caller-identity >/dev/null 2>&1 \
    || morrer "credenciais AWS não configuradas (rode 'aws configure')."

  # --- Descobrir o Auto Scaling Group ---------------------------------------
  if [[ -z "$ASG_NOME" ]]; then
    info "Procurando o Auto Scaling Group do SalaViva..."
    ASG_NOME="$(aws autoscaling describe-auto-scaling-groups \
      --region "$REGIAO" \
      --query "AutoScalingGroups[?contains(AutoScalingGroupName, 'salaviva')].AutoScalingGroupName | [0]" \
      --output text 2>/dev/null || true)"
  fi
  [[ -n "$ASG_NOME" && "$ASG_NOME" != "None" ]] \
    || morrer "ASG não encontrado. Informe com --asg NOME ou exporte SALAVIVA_ASG_NAME."

  # --- Descobrir a URL do ALB, se não informada ------------------------------
  if [[ "$URL_BASE" == "http://localhost:8080" ]]; then
    local dns
    dns="$(aws elbv2 describe-load-balancers --region "$REGIAO" \
      --query "LoadBalancers[?contains(LoadBalancerName, 'salaviva')].DNSName | [0]" \
      --output text 2>/dev/null || true)"
    if [[ -n "$dns" && "$dns" != "None" ]]; then
      URL_BASE="http://${dns}"
    else
      morrer "URL do balanceador desconhecida. Informe com --url http://<dns-do-alb>."
    fi
  fi

  titulo "Simulação de falha — AWS (Auto Scaling Group)"
  info "ASG ................... ${NEG}${ASG_NOME}${FIM}"
  info "Balanceador ........... ${URL_BASE}"
  info "Região ................ ${REGIAO}"
  printf '\n'

  # --- Instâncias InService --------------------------------------------------
  local instancias
  instancias="$(aws autoscaling describe-auto-scaling-groups \
    --region "$REGIAO" --auto-scaling-group-names "$ASG_NOME" \
    --query "AutoScalingGroups[0].Instances[?LifecycleState=='InService'].InstanceId" \
    --output text 2>/dev/null | tr '\t' '\n' | grep -v '^$' || true)"
  [[ -n "$instancias" ]] || morrer "nenhuma instância InService no ASG '${ASG_NOME}'."

  local total_instancias; total_instancias="$(grep -c . <<<"$instancias")"
  (( total_instancias > 1 )) \
    || morrer "só há uma instância InService; derrubá-la deixaria o serviço sem capacidade."

  printf '%s\n' "${NEG}  Instâncias InService (${total_instancias}):${FIM}"
  while IFS= read -r id; do printf '    %s\n' "$id"; done <<<"$instancias"
  printf '\n'

  local nos_antes total_antes
  nos_antes="$(consultar_nos "$URL_BASE")"
  total_antes="$(grep -c . <<<"$nos_antes" || true)"
  printf '%s\n' "${NEG}  Cluster ANTES da falha (${total_antes} nós registrados):${FIM}"
  tabela_nos "$URL_BASE"
  printf '\n'

  # Alvo: a instância indicada, ou a primeira da lista.
  local instancia_alvo
  if [[ -n "$ALVO" ]]; then
    grep -qx "$ALVO" <<<"$instancias" || morrer "instância '$ALVO' não está InService no ASG."
    instancia_alvo="$ALVO"
  else
    instancia_alvo="$(head -n1 <<<"$instancias")"
  fi

  confirmar "Encerrar DE VERDADE a instância ${instancia_alvo}?" || { aviso "Cancelado."; exit 0; }

  local t0; t0="$(agora)"
  info "Encerrando ${NEG}${instancia_alvo}${FIM} com 'aws ec2 terminate-instances'..."
  aws ec2 terminate-instances --region "$REGIAO" --instance-ids "$instancia_alvo" \
    --query 'TerminatingInstances[0].[InstanceId,CurrentState.Name]' --output text
  ok "Instância encerrada em t+0s. O ALB deve reprovar seu health check em ~15-30 s."
  printf '\n'

  # O node_id na EC2 é o ID da instância, injetado por user_data — por isso dá
  # para correlacionar diretamente o que sumiu do painel com o que foi
  # terminado. Se o Terraform usar outro esquema de node_id, a detecção recai
  # sobre a contagem de nós, tratada abaixo.
  local decorrido restantes total_agora
  info "Aguardando o registro de nós refletir a perda..."
  while :; do
    decorrido=$(( $(agora) - t0 ))
    restantes="$(consultar_nos "$URL_BASE")"
    total_agora="$(grep -c . <<<"$restantes" || true)"
    if ! grep -q "$instancia_alvo" <<<"$restantes" && (( total_agora < total_antes )); then
      T_DETECCAO="t+${decorrido}s"
      printf '\n'
      ok "$(printf "Cluster caiu para %s nós em ${NEG}t+%ss${FIM}." "$total_agora" "$decorrido")"
      break
    fi
    printf '\r  %s t+%-4ss %s nós registrados...   ' "${CINZ}·${FIM}" "$decorrido" "$total_agora"
    if (( decorrido > LIMITE )); then
      printf '\n'; aviso "Sem queda observada em ${LIMITE}s."; break
    fi
    sleep "$INTERVALO"
  done
  printf '\n'

  # --- Reposição pelo ASG -----------------------------------------------------
  info "Aguardando o Auto Scaling provisionar a substituta (meta: < 3 min, NFR de confiabilidade)..."
  local novos
  while :; do
    decorrido=$(( $(agora) - t0 ))
    restantes="$(consultar_nos "$URL_BASE")"
    total_agora="$(grep -c . <<<"$restantes" || true)"

    # "Substituto" = node_id presente agora que não existia antes da falha.
    novos="$(comm -13 <(sort <<<"$nos_antes") <(sort <<<"$restantes") | grep -v '^$' || true)"
    if [[ -n "$novos" && -z "$T_REPOSICAO" ]]; then
      T_REPOSICAO="t+${decorrido}s"
      printf '\n'
      ok "$(printf "Nó substituto no ar em ${NEG}t+%ss${FIM}: %s" "$decorrido" "$(tr '\n' ' ' <<<"$novos")")"
    fi

    if (( total_agora >= total_antes )); then
      T_QUORUM="t+${decorrido}s"
      printf '\n'
      ok "$(printf "Capacidade restabelecida em ${NEG}t+%ss${FIM} (%s nós)." "$decorrido" "$total_agora")"
      break
    fi

    printf '\r  %s t+%-4ss %s/%s nós · aguardando o ASG...   ' \
      "${CINZ}·${FIM}" "$decorrido" "$total_agora" "$total_antes"
    if (( decorrido > LIMITE )); then
      printf '\n'; aviso "Tempo limite de ${LIMITE}s sem capacidade completa. Verifique o console do ASG."
      break
    fi
    sleep "$INTERVALO"
  done

  printf '\n%s\n' "${NEG}  Cluster DEPOIS da recuperação:${FIM}"
  tabela_nos "$URL_BASE"

  local total_depois
  total_depois="$(consultar_nos "$URL_BASE" | grep -c . || true)"
  relatorio "$instancia_alvo" "$total_antes" "$total_depois"

  info "Estado atual do ASG:"
  aws autoscaling describe-auto-scaling-groups --region "$REGIAO" \
    --auto-scaling-group-names "$ASG_NOME" \
    --query "AutoScalingGroups[0].Instances[].[InstanceId,LifecycleState,HealthStatus]" \
    --output table
}

# --- Ponto de entrada --------------------------------------------------------
case "$MODO" in
  local) modo_local ;;
  aws)   modo_aws   ;;
esac
