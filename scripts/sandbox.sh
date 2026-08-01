#!/usr/bin/env bash
# =============================================================================
# SalaViva — ciclo de vida do ambiente na AWS Academy Sandbox
#
# A sandbox expira a sessão a cada ~3 horas: as credenciais morrem, as
# instâncias são paradas e o Auto Scaling começa a substituí-las — cobrando por
# capacidade que ninguém usa. O ciclo saudável, portanto, é subir e destruir a
# cada sessão, e não deixar o ambiente de pé.
#
# Este script existe para que esse ciclo custe um comando em vez de dez.
#
#   ./scripts/sandbox.sh status    diagnóstico: credenciais, tempo, o que está no ar
#   ./scripts/sandbox.sh subir     apply + espera os nós ficarem saudáveis
#   ./scripts/sandbox.sh descer    destrói tudo (rode SEMPRE ao terminar)
#   ./scripts/sandbox.sh urls      reimprime os endereços
#
# Uso típico de uma sessão de estudo:
#   1. Start Lab no Vocareum, esperar o verde
#   2. AWS Details > AWS CLI > Show, copiar, e:  pbpaste > ~/.aws/credentials
#   3. ./scripts/sandbox.sh subir
#   4. ... trabalhar ...
#   5. ./scripts/sandbox.sh descer
# =============================================================================

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${RAIZ}/infra/terraform-sandbox"
REGIAO="${AWS_REGION:-us-east-1}"

if [[ -t 1 ]]; then
  VERDE=$'\033[32m'; VERM=$'\033[31m'; AMAR=$'\033[33m'
  CINZA=$'\033[90m'; NEG=$'\033[1m'; FIM=$'\033[0m'
else
  VERDE=''; VERM=''; AMAR=''; CINZA=''; NEG=''; FIM=''
fi

ok()     { printf '  %s✓%s %s\n' "$VERDE" "$FIM" "$*"; }
falha()  { printf '  %s✗%s %s\n' "$VERM" "$FIM" "$*"; }
aviso()  { printf '  %s!%s %s\n' "$AMAR" "$FIM" "$*"; }
info()   { printf '  %s\n' "$*"; }
titulo() { printf '\n%s%s%s\n' "$NEG" "$*" "$FIM"; }
morrer() { printf '\n%serro:%s %s\n\n' "$VERM" "$FIM" "$*" >&2; exit 1; }

# -----------------------------------------------------------------------------
# Pré-condições
# -----------------------------------------------------------------------------

exige_ferramentas() {
  local faltando=()
  for f in aws terraform; do
    command -v "$f" >/dev/null 2>&1 || faltando+=("$f")
  done
  if ((${#faltando[@]})); then
    morrer "faltam ferramentas: ${faltando[*]}
  Instale com:  brew install awscli && brew install hashicorp/tap/terraform"
  fi
}

# Devolve 0 se as credenciais respondem, 1 caso contrário.
credenciais_validas() {
  aws sts get-caller-identity --region "$REGIAO" >/dev/null 2>&1
}

instrucao_credenciais() {
  cat <<EOF

  As credenciais da sandbox expiraram (ou nunca foram configuradas).
  Elas são temporárias e morrem junto com a sessão do laboratório.

  Para renovar:
    1. No Vocareum, clique em ${NEG}Start Lab${FIM} e espere o indicador ficar ${VERDE}verde${FIM}
    2. Clique em ${NEG}AWS Details${FIM} > ${NEG}AWS CLI${FIM} > ${NEG}Show${FIM}
    3. Copie o bloco inteiro (começa com [default]) e rode:

         ${CINZA}mkdir -p ~/.aws && pbpaste > ~/.aws/credentials${FIM}

    4. Rode este comando de novo.

EOF
}

exige_credenciais() {
  credenciais_validas || { instrucao_credenciais; exit 1; }
}

exige_tfvars() {
  [[ -f "${TF_DIR}/terraform.tfvars" ]] || morrer "falta ${TF_DIR}/terraform.tfvars
  Crie com:
    cd ${TF_DIR}
    cp terraform.tfvars.example terraform.tfvars
    openssl rand -base64 32      # cole o resultado em jwt_secret"
}

tf() { terraform -chdir="$TF_DIR" "$@"; }

saida_tf() { tf output -raw "$1" 2>/dev/null || true; }

# -----------------------------------------------------------------------------
# status
# -----------------------------------------------------------------------------

cmd_status() {
  titulo "Credenciais"
  if credenciais_validas; then
    local conta
    conta="$(aws sts get-caller-identity --region "$REGIAO" --query Account --output text)"
    ok "válidas — conta ${conta}"
  else
    falha "expiradas ou ausentes"
    instrucao_credenciais
    return 0
  fi

  titulo "Instance profile (necessária para gravar no DynamoDB)"
  local perfis
  perfis="$(aws iam list-instance-profiles \
    --query 'InstanceProfiles[].InstanceProfileName' --output text 2>/dev/null || true)"
  if grep -qw "LabInstanceProfile" <<<"$perfis"; then
    ok "LabInstanceProfile disponível"
  elif [[ -n "$perfis" ]]; then
    aviso "LabInstanceProfile não encontrada. Disponíveis: ${perfis}"
  else
    aviso "nenhuma instance profile — use instance_profile = \"\" e o histórico ficará desligado"
  fi

  titulo "Recursos no ar"
  local instancias alb
  instancias="$(aws ec2 describe-instances --region "$REGIAO" \
    --filters "Name=tag:Project,Values=SalaViva" "Name=instance-state-name,Values=running,pending" \
    --query 'length(Reservations[].Instances[])' --output text 2>/dev/null || echo 0)"
  alb="$(aws elbv2 describe-load-balancers --region "$REGIAO" \
    --query "length(LoadBalancers[?starts_with(LoadBalancerName, 'salaviva')])" \
    --output text 2>/dev/null || echo 0)"

  if [[ "$instancias" == "0" && "$alb" == "0" ]]; then
    ok "nada de pé — não está consumindo crédito"
  else
    aviso "${instancias} instância(s) e ${alb} balanceador(es) ativos"
    info "${CINZA}consumindo ~US\$ 0,07/hora · rode './scripts/sandbox.sh descer' ao terminar${FIM}"
  fi

  local url
  url="$(saida_tf chat_url)"
  [[ -n "$url" ]] && { titulo "Endereços"; info "chat      ${url}"; info "dashboard ${url}/dashboard"; }
  printf '\n'
}

# -----------------------------------------------------------------------------
# subir
# -----------------------------------------------------------------------------

aguardar_nos() {
  local url="$1" esperados="$2" limite=900 t=0 vivos=0
  printf '\n  Aguardando os nós entrarem em serviço (limite %ss)\n' "$limite"
  printf '  %sO primeiro boot constrói a imagem Docker na instância e é lento.%s\n' "$CINZA" "$FIM"

  while ((t < limite)); do
    vivos="$(curl -fsS --max-time 5 "${url}/api/nodes" 2>/dev/null \
      | python3 -c 'import sys,json;print(json.load(sys.stdin).get("count",0))' 2>/dev/null || echo 0)"
    printf '\r  t+%-4ss  %s/%s nós saudáveis    ' "$t" "$vivos" "$esperados"
    (( vivos >= esperados )) && { printf '\n'; ok "cluster completo"; return 0; }
    sleep 10; t=$((t + 10))
  done

  printf '\n'
  aviso "tempo limite com ${vivos}/${esperados} nós"
  info "Investigue: EC2 > Connect > EC2 Instance Connect, e depois:"
  info "  ${CINZA}sudo tail -100 /var/log/salaviva-boot.log${FIM}"
  return 1
}

cmd_subir() {
  exige_credenciais
  exige_tfvars

  titulo "Provisionando"
  tf init -input=false >/dev/null
  tf apply -auto-approve -input=false

  local url desejados
  url="$(saida_tf chat_url)"
  desejados="$(tf output -json 2>/dev/null \
    | python3 -c 'import sys,json;print(json.load(sys.stdin).get("nos_desejados",{}).get("value",3))' 2>/dev/null || echo 3)"
  [[ -n "$url" ]] || morrer "o apply não devolveu chat_url"

  aguardar_nos "$url" "$desejados" || true

  titulo "Pronto"
  info "chat      ${NEG}${url}${FIM}"
  info "dashboard ${url}/dashboard"
  printf '\n  %sDemo de falha:%s\n' "$NEG" "$FIM"
  info "  export SALAVIVA_LB_URL=\"${url}\""
  info "  export SALAVIVA_ASG_NAME=\"$(saida_tf asg_name)\""
  info "  ./scripts/kill_node.sh --aws"
  printf '\n  %sAo terminar:%s ./scripts/sandbox.sh descer\n\n' "$AMAR" "$FIM"
}

# -----------------------------------------------------------------------------
# descer
# -----------------------------------------------------------------------------

cmd_descer() {
  exige_credenciais

  titulo "Destruindo o ambiente"
  info "Isto remove instâncias, balanceador, VPC e tabelas do DynamoDB."
  info "${CINZA}É o passo que protege seu orçamento de US\$ 20.${FIM}"
  printf '\n'

  tf destroy -auto-approve -input=false

  titulo "Conferindo se sobrou algo"
  local restantes
  restantes="$(aws ec2 describe-instances --region "$REGIAO" \
    --filters "Name=tag:Project,Values=SalaViva" "Name=instance-state-name,Values=running,pending" \
    --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null || true)"

  if [[ -z "$restantes" ]]; then
    ok "nenhuma instância SalaViva ativa"
  else
    aviso "ainda ativas: ${restantes}"
    info "Encerre pelo console da EC2 antes de sair."
  fi

  printf '\n  Agora clique em %sEnd Lab%s no Vocareum.\n\n' "$NEG" "$FIM"
}

# -----------------------------------------------------------------------------

cmd_urls() {
  local url; url="$(saida_tf chat_url)"
  [[ -n "$url" ]] || morrer "nenhum ambiente provisionado (rode: ./scripts/sandbox.sh subir)"
  printf '\nchat      %s\ndashboard %s/dashboard\nasg       %s\n\n' \
    "$url" "$url" "$(saida_tf asg_name)"
}

cmd_ajuda() {
  cat <<EOF

${NEG}SalaViva — ambiente na AWS Academy Sandbox${FIM}

  ${NEG}status${FIM}   diagnóstico: credenciais, instance profile, o que está no ar
  ${NEG}subir${FIM}    provisiona e espera os nós ficarem saudáveis
  ${NEG}descer${FIM}   destrói tudo — ${AMAR}rode SEMPRE ao terminar a sessão${FIM}
  ${NEG}urls${FIM}     reimprime os endereços do ambiente

${NEG}Ciclo de uma sessão${FIM}

  1. Start Lab no Vocareum, esperar o indicador ficar verde
  2. AWS Details > AWS CLI > Show, copiar o bloco, e:
       ${CINZA}mkdir -p ~/.aws && pbpaste > ~/.aws/credentials${FIM}
  3. ${CINZA}./scripts/sandbox.sh subir${FIM}
  4. ... trabalhar ...
  5. ${CINZA}./scripts/sandbox.sh descer${FIM}  e depois End Lab

A sessão expira em ~3 horas e as credenciais morrem junto. Se um comando falhar
com ExpiredToken, refaça o passo 2 — nada se perde.

EOF
}

case "${1:-ajuda}" in
  # A ajuda precisa funcionar em máquina sem nada instalado — é justamente onde
  # alguém a lê pela primeira vez.
  ajuda|-h|--help) cmd_ajuda ;;
  status)          exige_ferramentas; cmd_status ;;
  subir|up)        exige_ferramentas; cmd_subir ;;
  descer|down)     exige_ferramentas; cmd_descer ;;
  urls)            exige_ferramentas; cmd_urls ;;
  *)               morrer "comando desconhecido: '$1' (veja: ./scripts/sandbox.sh ajuda)" ;;
esac
