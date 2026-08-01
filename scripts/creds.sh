#!/usr/bin/env bash
# =============================================================================
# Grava as credenciais temporárias da AWS Academy a partir do clipboard.
#
# POR QUE ESTE SCRIPT EXISTE
#
# O procedimento óbvio — `pbpaste > ~/.aws/credentials` — tem uma armadilha: o
# comando em si costuma ser copiado de algum lugar, e essa cópia SUBSTITUI as
# credenciais no clipboard. O resultado é um arquivo contendo o próprio comando,
# e um erro obscuro depois ("Unable to parse config file").
#
# A solução é um comando curto o suficiente para ser digitado, que valida o
# conteúdo do clipboard antes de escrever qualquer coisa.
#
# Uso:
#   1. No Vocareum: AWS Details > AWS CLI > Show, e copie o bloco
#   2. Digite (não cole):  make creds
# =============================================================================

set -euo pipefail

if [[ -t 1 ]]; then
  VERDE=$'\033[32m'; VERM=$'\033[31m'; AMAR=$'\033[33m'; NEG=$'\033[1m'; FIM=$'\033[0m'
else
  VERDE=''; VERM=''; AMAR=''; NEG=''; FIM=''
fi

ARQUIVO="${HOME}/.aws/credentials"

morrer() { printf '\n  %s✗%s %s\n\n' "$VERM" "$FIM" "$*" >&2; exit 1; }

command -v pbpaste >/dev/null 2>&1 || morrer "pbpaste não encontrado (este script é para macOS)"

CONTEUDO="$(pbpaste)"

# --- Validação antes de escrever ---------------------------------------------
# Escrever primeiro e descobrir o erro depois é o que produz o modo de falha
# descrito no cabeçalho. Aqui, um clipboard errado não toca no arquivo.

if [[ -z "${CONTEUDO// }" ]]; then
  morrer "o clipboard está vazio.
  Copie o bloco em: Vocareum > AWS Details > AWS CLI > Show"
fi

faltando=()
for chave in aws_access_key_id aws_secret_access_key aws_session_token; do
  grep -q "^[[:space:]]*${chave}[[:space:]]*=" <<<"$CONTEUDO" || faltando+=("$chave")
done

if ((${#faltando[@]})); then
  printf '\n  %s✗%s O clipboard não contém credenciais da AWS.\n' "$VERM" "$FIM"
  printf '     Faltam: %s\n\n' "${faltando[*]}"
  printf '  O que está no clipboard agora (primeiros 60 caracteres):\n'
  printf '     %s%.60s%s\n\n' "$AMAR" "$CONTEUDO" "$FIM"
  printf '  %sCausa mais comum:%s você copiou este comando, e a cópia substituiu\n' "$NEG" "$FIM"
  printf '  as credenciais no clipboard. Copie o bloco no Vocareum\n'
  printf '  (AWS Details > AWS CLI > Show) e DIGITE "make creds" em vez de colar.\n\n'
  printf '  %sO arquivo existente não foi alterado.%s\n\n' "$VERDE" "$FIM"
  exit 1
fi

grep -q '^\[' <<<"$CONTEUDO" || morrer "o bloco não tem um perfil (a primeira linha deve ser [default])"

# --- Escrita ------------------------------------------------------------------

mkdir -p "$(dirname "$ARQUIVO")"

if [[ -f "$ARQUIVO" ]]; then
  cp "$ARQUIVO" "${ARQUIVO}.bak"
fi

printf '%s\n' "$CONTEUDO" > "$ARQUIVO"
chmod 600 "$ARQUIVO"

printf '\n  %s✓%s credenciais gravadas em %s\n' "$VERDE" "$FIM" "$ARQUIVO"

# --- Verificação --------------------------------------------------------------

if ! command -v aws >/dev/null 2>&1; then
  printf '  %s!%s AWS CLI não instalada — não deu para verificar (brew install awscli)\n\n' "$AMAR" "$FIM"
  exit 0
fi

printf '  verificando com a AWS...\n'
if saida="$(aws sts get-caller-identity --region "${AWS_REGION:-us-east-1}" --output text 2>&1)"; then
  conta="$(awk '{print $1}' <<<"$saida")"
  printf '  %s✓%s válidas — conta %s%s%s\n\n' "$VERDE" "$FIM" "$NEG" "$conta" "$FIM"
  printf '  Próximo passo: %smake sandbox-up%s\n\n' "$NEG" "$FIM"
else
  printf '\n  %s✗%s a AWS recusou as credenciais:\n     %s\n\n' "$VERM" "$FIM" "$(head -2 <<<"$saida")"
  printf '  Se a mensagem citar expiração, a sessão do laboratório acabou:\n'
  printf '  clique em Start Lab, copie o bloco novo e rode "make creds" de novo.\n\n'
  exit 1
fi
