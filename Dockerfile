# syntax=docker/dockerfile:1.7
# =============================================================================
# SalaViva — imagem do nó de aplicação.
#
# A MESMA imagem roda nos três contextos do projeto: Docker Compose local,
# instância EC2 do Auto Scaling Group e execução avulsa em modo standalone.
# Nada aqui detecta o ambiente — a diferença vem sempre por variável de
# ambiente (ver src/salaviva/config.py). É isso que faz o cluster local ter
# paridade real com a nuvem: o que se ensaia no notebook é o mesmo binário que
# o ALB recebe.
#
# Build multi-estágio: o estágio `builder` carrega o uv e o toolchain de
# instalação; o estágio final carrega apenas o venv pronto e o Python. O
# resultado é uma imagem menor e sem ferramenta de build exposta em produção.
# =============================================================================

# -----------------------------------------------------------------------------
# Estágio 1 — builder: resolve dependências e monta o virtualenv em /opt/venv.
# -----------------------------------------------------------------------------
FROM python:3.13-slim AS builder

# uv oficial, copiado como binário estático da imagem da Astral. Evita instalar
# pip/virtualenv/poetry empilhados só para preparar o ambiente.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# --- Camada de dependências -------------------------------------------------
# Copiamos apenas o manifesto antes do código-fonte. Enquanto pyproject.toml e
# uv.lock não mudarem, o Docker reaproveita esta camada em cache e o rebuild
# após uma alteração de código leva segundos em vez de minutos — o que importa
# quando se está ajustando algo minutos antes da apresentação.
#
# `uv.loc[k]` é fonte OPCIONAL: o padrão com colchetes não quebra o build se o
# lockfile ainda não existir no repositório (o uv o gera na hora). Se existir, a
# instalação é determinística. Precisa estar no MESMO COPY dos arquivos
# obrigatórios — um COPY cujo único padrão não casa com nada falha.
COPY pyproject.toml README.md uv.loc[k] ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project

# --- Camada de código -------------------------------------------------------
COPY src/ ./src/

# `--no-editable`: instala o pacote de verdade dentro de /opt/venv em vez de um
# ponteiro para /build/src. Sem isso o venv copiado para o estágio final
# apontaria para um diretório que não existe mais.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable

# -----------------------------------------------------------------------------
# Estágio 2 — runtime: só o que é preciso para servir.
# -----------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.title="SalaViva" \
      org.opencontainers.image.description="Chat distribuído em tempo real com salas (Pub/Sub)" \
      org.opencontainers.image.source="https://github.com/salaviva/salaviva" \
      org.opencontainers.image.licenses="MIT"

# curl é a única dependência de sistema adicionada: serve ao HEALTHCHECK abaixo,
# ao health check do Compose e à depuração dentro do container durante a demo.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Usuário não-root. Se um dia alguém explorar uma falha no parser do protocolo,
# o processo comprometido não é root dentro do container.
RUN groupadd --system --gid 1001 salaviva \
 && useradd  --system --uid 1001 --gid salaviva --create-home --home-dir /home/salaviva salaviva

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SALAVIVA_PORT=8000

COPY --from=builder --chown=salaviva:salaviva /opt/venv /opt/venv

WORKDIR /app
USER salaviva

EXPOSE 8000

# Liveness deliberadamente em /healthz, e não em /readyz.
#
# Este HEALTHCHECK responde à pergunta "devo reiniciar este container?". Um nó
# que perdeu o Redis não deve ser reiniciado — reiniciar não traz o Redis de
# volta — ele deve apenas parar de receber tráfego novo, e quem decide isso é o
# balanceador consultando /readyz. Misturar os dois transformaria uma falha de
# dependência em um laço de reinício. Ver memory-bank/standards/system-architecture.md.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${SALAVIVA_PORT}/healthz" || exit 1

# UM worker por container, deliberadamente (ADR-005 / tech-stack.md).
#
# Cada nó mantém em memória o mapa de conexões WebSocket e o relógio de Lamport.
# Dois workers no mesmo container seriam dois processos com o MESMO node_id e
# relógios divergentes, sem canal entre eles — o modelo formal de Lamport
# pressupõe a correspondência 1:1 entre processo e relógio. Escala-se somando
# containers/instâncias, nunca workers.
#
# --forwarded-allow-ips=*: o nó só recebe tráfego do ALB (na AWS) ou do nginx
# (no Compose), ambos dentro da rede privada. Confiar nos cabeçalhos
# X-Forwarded-* deles é o que faz o IP real do cliente chegar ao log.
CMD ["uvicorn", "salaviva.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--forwarded-allow-ips", "*", \
     "--no-access-log"]
