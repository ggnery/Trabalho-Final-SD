#!/usr/bin/env bash
# =============================================================================
# build_push.sh — constrói a imagem do nó e a envia para o Amazon ECR.
#
# Por que ECR e não Docker Hub: as instâncias EC2 estão em subrede pública sem
# NAT Gateway (ADR-006) e autenticam no ECR pelo instance profile do IAM. Isso
# evita embutir credencial de registro no `user_data` — que ficaria legível a
# quem tivesse acesso à console da instância.
#
# A tag padrão é o hash curto do commit. Duas razões: (1) permite dizer, olhando
# uma instância, exatamente qual código está rodando; (2) força o `docker pull`
# a de fato baixar a versão nova — com `latest` fixo, uma instância que já tem a
# camada em cache pode subir código velho, e depurar isso no meio da
# apresentação é o pior cenário possível.
#
# Uso:
#   ./scripts/build_push.sh
#   ./scripts/build_push.sh --tag v1.0.0
#   ./scripts/build_push.sh --repo 123456789012.dkr.ecr.us-east-1.amazonaws.com/salaviva
#   ./scripts/build_push.sh --sem-cache
#
# Variáveis reconhecidas: SALAVIVA_ECR_REPO, AWS_REGION.
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

REPO="${SALAVIVA_ECR_REPO:-}"
REGIAO="${AWS_REGION:-us-east-1}"
TAG=""
PLATAFORMA="linux/amd64"
CACHE_ARGS=()

uso() { sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)             REPO="${2:?--repo exige um valor}"; shift ;;
    --tag)              TAG="${2:?--tag exige um valor}"; shift ;;
    --regiao|--region)  REGIAO="${2:?--regiao exige um valor}"; shift ;;
    --plataforma|--platform) PLATAFORMA="${2:?--plataforma exige um valor}"; shift ;;
    --sem-cache|--no-cache)  CACHE_ARGS+=(--no-cache) ;;
    -h|--help)          uso ;;
    *) morrer "opção desconhecida: $1 (use --help)" ;;
  esac
  shift
done

command -v docker >/dev/null 2>&1 || morrer "docker não encontrado."
command -v aws    >/dev/null 2>&1 || morrer "AWS CLI não encontrada."
docker info >/dev/null 2>&1 || morrer "o daemon do Docker não está rodando."
aws sts get-caller-identity >/dev/null 2>&1 || morrer "credenciais AWS não configuradas."

# --- Descoberta do repositório ECR -------------------------------------------
# Ordem: parâmetro > variável de ambiente > output do Terraform > consulta ao
# próprio ECR. Assim o script funciona antes e depois de o Terraform existir.
if [[ -z "$REPO" ]]; then
  if [[ -d "${TF_DIR}/.terraform" ]] && command -v terraform >/dev/null 2>&1; then
    for nome in ecr_repository_url ecr_url repository_url ecr_repo_url; do
      REPO="$(terraform -chdir="$TF_DIR" output -raw "$nome" 2>/dev/null || true)"
      [[ -n "$REPO" ]] && break
    done
  fi
fi
if [[ -z "$REPO" ]]; then
  REPO="$(aws ecr describe-repositories --region "$REGIAO" \
    --query "repositories[?contains(repositoryName, 'salaviva')].repositoryUri | [0]" \
    --output text 2>/dev/null || true)"
  [[ "$REPO" == "None" ]] && REPO=""
fi
[[ -n "$REPO" ]] || morrer "repositório ECR não encontrado. Rode 'make tf-apply' ou informe --repo."

REGISTRO="${REPO%%/*}"   # 123456789012.dkr.ecr.us-east-1.amazonaws.com

# --- Tag ---------------------------------------------------------------------
if [[ -z "$TAG" ]]; then
  if git -C "$RAIZ" rev-parse --short HEAD >/dev/null 2>&1; then
    TAG="$(git -C "$RAIZ" rev-parse --short HEAD)"
    if [[ -n "$(git -C "$RAIZ" status --porcelain 2>/dev/null)" ]]; then
      TAG="${TAG}-sujo"
      aviso "Há alterações não commitadas: a tag recebeu o sufixo '-sujo'."
    fi
  else
    TAG="$(date +%Y%m%d%H%M%S)"
  fi
fi

printf '\n%s\n' "${NEG}${AZUL}── Build e push da imagem do SalaViva ──────────────────────────${FIM}"
info "Repositório .... ${REPO}"
info "Tag ............ ${NEG}${TAG}${FIM} (e 'latest')"
info "Plataforma ..... ${PLATAFORMA}"
printf '\n'

# --- Login -------------------------------------------------------------------
info "Autenticando no ECR..."
aws ecr get-login-password --region "$REGIAO" \
  | docker login --username AWS --password-stdin "$REGISTRO" >/dev/null
ok "Autenticado em ${REGISTRO}."

# --- Build -------------------------------------------------------------------
# --platform linux/amd64 é obrigatório quando se constrói em Mac Apple Silicon:
# sem ele, a imagem sai arm64 e a instância t3.micro (x86_64) responde com
# "exec format error" — falha que só aparece na EC2, nunca no notebook.
info "Construindo a imagem (${PLATAFORMA})..."
docker build \
  --platform "$PLATAFORMA" \
  "${CACHE_ARGS[@]}" \
  --tag "${REPO}:${TAG}" \
  --tag "${REPO}:latest" \
  --file "${RAIZ}/Dockerfile" \
  "$RAIZ"
ok "Imagem construída."

# --- Push --------------------------------------------------------------------
info "Enviando ${REPO}:${TAG}..."
docker push "${REPO}:${TAG}"
info "Enviando ${REPO}:latest..."
docker push "${REPO}:latest"

printf '\n'
ok "Imagem publicada: ${NEG}${REPO}:${TAG}${FIM}"
printf '%s\n' "${CINZ}  Próximo passo: ./scripts/deploy.sh --tag ${TAG}${FIM}"
printf '%s\n' "${CINZ}  (ou 'make deploy' para build + push + instance refresh)${FIM}"
printf '\n'

# Última linha em formato estável: deploy.sh a consome para saber o que subiu.
echo "IMAGEM=${REPO}:${TAG}"
