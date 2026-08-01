#!/usr/bin/env bash
# =============================================================================
# teardown.sh — destrói TODA a infraestrutura AWS do SalaViva.
#
# Este script existe por uma razão prática e uma acadêmica.
#
# A prática: a conta é pessoal e o Free Tier tem teto. Três t3.micro, um ALB e
# um cache.t3.micro rodando esquecidos depois da apresentação viram cobrança
# real. O compromisso registrado em ADR-001 é justamente "terraform destroy
# documentado" como mitigação do custo de manter EC2 24/7.
#
# A acadêmica: infraestrutura descartável é a prova de que ela é de fato código.
# Se o ambiente pode ser destruído e recriado por comando, então o `.tf` é a
# fonte da verdade — e não um registro parcial de cliques na console.
#
# A confirmação é deliberadamente chata: exige digitar a palavra DESTRUIR. Um
# "s/N" seria fácil demais de responder no automático com a mão no Enter.
#
# Uso:
#   ./scripts/teardown.sh
#   ./scripts/teardown.sh --com-ecr       # apaga também as imagens do ECR
#   ./scripts/teardown.sh --forcar        # sem confirmação (CI; use com cuidado)
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

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${RAIZ}/infra/terraform"

REGIAO="${AWS_REGION:-us-east-1}"
FORCAR=0
LIMPAR_ECR=0

uso() { sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --forcar|--force)  FORCAR=1 ;;
    --com-ecr)         LIMPAR_ECR=1 ;;
    --regiao|--region) REGIAO="${2:?--regiao exige um valor}"; shift ;;
    -h|--help)         uso ;;
    *) morrer "opção desconhecida: $1 (use --help)" ;;
  esac
  shift
done

command -v terraform >/dev/null 2>&1 || morrer "terraform não encontrado."
[[ -d "$TF_DIR" ]] || morrer "diretório não encontrado: ${TF_DIR}"

printf '\n%s\n' "${NEG}${VERM}╔══════════════════════════════════════════════════════════════╗${FIM}"
printf '%s\n'   "${NEG}${VERM}║  DESTRUIÇÃO DA INFRAESTRUTURA AWS DO SALAVIVA                ║${FIM}"
printf '%s\n\n' "${NEG}${VERM}╚══════════════════════════════════════════════════════════════╝${FIM}"

# --- O que será destruído -----------------------------------------------------
info "Calculando o plano de destruição (terraform plan -destroy)..."
if ! terraform -chdir="$TF_DIR" plan -destroy -no-color -input=false 2>/dev/null \
     | grep -E '^Plan:|^No changes' || true; then
  aviso "Não foi possível calcular o plano — talvez falte 'terraform init'."
fi

printf '\n%s\n' "${NEG}  Serão removidos (entre outros):${FIM}"
printf '%s\n' "    · Auto Scaling Group e as instâncias EC2 em execução"
printf '%s\n' "    · Application Load Balancer e target group"
printf '%s\n' "    · Cluster ElastiCache for Redis  ${VERM}(o histórico de seq some)${FIM}"
printf '%s\n' "    · Tabelas DynamoDB               ${VERM}(as mensagens somem)${FIM}"
printf '%s\n' "    · VPC, subredes, security groups, IAM e repositório ECR"
printf '\n'
printf '%s\n' "${CINZ}  Nada disso é recuperável. Recriar é 'make tf-apply && make deploy'.${FIM}"
printf '\n'

# --- Confirmação explícita ----------------------------------------------------
if [[ "$FORCAR" -eq 0 ]]; then
  printf '%s' "${AMAR}?${FIM} Digite ${NEG}DESTRUIR${FIM} para confirmar: "
  read -r RESPOSTA || true
  [[ "$RESPOSTA" == "DESTRUIR" ]] || { aviso "Confirmação não conferiu. Nada foi alterado."; exit 0; }
else
  aviso "--forcar: pulando a confirmação."
fi

# --- ECR ----------------------------------------------------------------------
# O Terraform recusa-se a apagar um repositório ECR que ainda tem imagens
# (a menos que force_delete esteja ligado). Esvaziar antes evita um destroy
# que falha pela metade e deixa recursos órfãos cobrando.
if [[ "$LIMPAR_ECR" -eq 1 ]]; then
  if command -v aws >/dev/null 2>&1; then
    REPO_NOME="$(aws ecr describe-repositories --region "$REGIAO" \
      --query "repositories[?contains(repositoryName, 'salaviva')].repositoryName | [0]" \
      --output text 2>/dev/null || true)"
    if [[ -n "$REPO_NOME" && "$REPO_NOME" != "None" ]]; then
      info "Apagando as imagens do repositório ECR '${REPO_NOME}'..."
      IDS="$(aws ecr list-images --region "$REGIAO" --repository-name "$REPO_NOME" \
        --query 'imageIds[*]' --output json 2>/dev/null || echo '[]')"
      if [[ "$IDS" != "[]" ]]; then
        aws ecr batch-delete-image --region "$REGIAO" \
          --repository-name "$REPO_NOME" --image-ids "$IDS" >/dev/null || true
        ok "Imagens removidas."
      else
        info "Nenhuma imagem a remover."
      fi
    fi
  else
    aviso "AWS CLI ausente: pulando a limpeza do ECR."
  fi
fi

# --- Destroy ------------------------------------------------------------------
info "Executando terraform destroy..."
INICIO="$(date +%s)"
terraform -chdir="$TF_DIR" destroy -auto-approve -input=false
DECORRIDO=$(( $(date +%s) - INICIO ))

printf '\n'
ok "Infraestrutura destruída em ${DECORRIDO}s."
printf '\n'
printf '%s\n' "${NEG}  Confira manualmente na console (recursos que o Terraform não gerencia):${FIM}"
printf '%s\n' "    · Snapshots do ElastiCache criados automaticamente"
printf '%s\n' "    · Grupos de log do CloudWatch (retêm dados e podem gerar custo)"
printf '%s\n' "    · Parâmetros do SSM criados fora do Terraform"
printf '%s\n' "    · Elastic IPs órfãos:  aws ec2 describe-addresses --region ${REGIAO}"
printf '\n'
printf '%s\n' "${CINZ}  A demonstração local continua disponível sem custo algum: 'make up'.${FIM}"
printf '\n'
