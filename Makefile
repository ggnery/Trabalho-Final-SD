# =============================================================================
# SalaViva — atalhos de operação.
#
# Objetivo deste arquivo: qualquer passo da apresentação cabe em um comando de
# uma linha. Sob pressão, na frente da turma, ninguém deve precisar lembrar de
# uma sequência de flags do docker/terraform/aws.
#
# Comece por:  make help
# =============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- Parâmetros sobrescrevíveis na linha de comando --------------------------
#   ex.: make logs SERVICO=node-b        make demo-kill ARGS="--aws"
COMPOSE       ?= docker compose
UV            ?= uv
TF_DIR        ?= infra/terraform
LB_URL        ?= http://localhost:8080
SERVICO       ?=
ARGS          ?=

# Cores para as mensagens dos alvos (não afetam a saída dos comandos).
AZUL  := \033[36m
VERDE := \033[32m
NEG   := \033[1m
FIM   := \033[0m

.PHONY: help install up down logs ps test test-unit test-integration test-e2e \
        lint fmt loadtest cli standalone demo-kill watch deploy build-push \
        tf-init tf-plan tf-apply tf-destroy teardown scripts-exec clean

# -----------------------------------------------------------------------------
help: ## Mostra esta ajuda (alvo padrão)
	@printf "\n$(NEG)SalaViva$(FIM) — chat distribuído em tempo real com salas\n\n"
	@printf "  uso: $(NEG)make <alvo>$(FIM)\n\n"
	@awk 'BEGIN {FS = ":.*?## "} \
	     /^[a-zA-Z0-9_-]+:.*?## / {printf "  $(AZUL)%-16s$(FIM) %s\n", $$1, $$2} \
	     /^# GRUPO: / {printf "\n$(NEG)%s$(FIM)\n", substr($$0, 10)}' $(MAKEFILE_LIST)
	@printf "\n  URLs com o cluster no ar: $(VERDE)$(LB_URL)$(FIM) (chat) · $(VERDE)$(LB_URL)/dashboard$(FIM) (painel de nós)\n\n"

# GRUPO: Ambiente de desenvolvimento

install: scripts-exec ## Instala dependências (uv) e prepara os scripts
	$(UV) sync --all-extras
	@printf "$(VERDE)Ambiente pronto.$(FIM) Rode 'make up' para subir o cluster local.\n"

scripts-exec: ## Dá permissão de execução aos scripts de operação
	@chmod +x scripts/*.sh 2>/dev/null || true
	@printf "$(VERDE)Scripts em scripts/ marcados como executáveis.$(FIM)\n"

# GRUPO: Cluster local (Docker Compose)

up: scripts-exec ## Sobe o cluster local: redis + dynamodb + 3 nós + nginx
	$(COMPOSE) up -d --build
	@printf "\n$(VERDE)Cluster no ar.$(FIM)\n"
	@printf "  Chat ............. $(LB_URL)\n"
	@printf "  Painel de nós .... $(LB_URL)/dashboard\n"
	@printf "  Nó A / B / C ..... http://localhost:8001 · :8002 · :8003\n"
	@printf "  Nós vivos ........ curl -s $(LB_URL)/api/nodes\n\n"

down: ## Derruba o cluster local e remove volumes (estado zerado)
	$(COMPOSE) down -v --remove-orphans
	@printf "$(VERDE)Cluster removido.$(FIM)\n"

logs: ## Acompanha os logs (make logs SERVICO=node-b para um só)
	$(COMPOSE) logs -f --tail=100 $(SERVICO)

ps: ## Estado dos containers do cluster
	$(COMPOSE) ps

standalone: ## Roda UM nó sem Redis nem AWS (plano B da apresentação)
	SALAVIVA_REDIS_URL=memory:// \
	SALAVIVA_PERSISTENCE_ENABLED=false \
	SALAVIVA_NODE_ID=node-standalone \
	SALAVIVA_LOG_JSON=false \
	$(UV) run uvicorn salaviva.main:app --host 0.0.0.0 --port 8000 --reload

# GRUPO: Demonstração e observação

watch: scripts-exec ## Painel de terminal com os nós vivos, atualizado a cada 2s
	./scripts/watch_cluster.sh --url $(LB_URL) $(ARGS)

demo-kill: scripts-exec ## Derruba um nó e cronometra a recuperação (ARGS="--aws" na nuvem)
	./scripts/kill_node.sh $(ARGS)

cli: ## Abre o cliente de terminal (ARGS="--user ana --room geral")
	@if [ -f client/cli/salaviva_cli.py ]; then \
	  $(UV) run python client/cli/salaviva_cli.py --url $(LB_URL) $(ARGS); \
	else \
	  printf "Cliente CLI ainda não existe em client/cli/salaviva_cli.py\n"; exit 1; \
	fi

# GRUPO: Qualidade

test: test-unit test-integration ## Testes unitários + integração (não exige cluster)

test-unit: ## Testes unitários do domínio (relógios, ordenação, envelope)
	$(UV) run pytest tests/unit -v

test-integration: ## Testes de integração multi-nó com adaptadores em memória
	$(UV) run pytest tests/integration -v

test-e2e: ## Testes ponta a ponta — EXIGE 'make up' antes
	@printf "Requer o cluster no ar (make up).\n"
	$(UV) run pytest tests/e2e -v -m e2e

lint: ## Verifica estilo e erros estáticos (ruff)
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## Formata o código e aplica correções automáticas (ruff)
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

loadtest: ## Teste de carga (≥1000 conexões) e gráfico de latência
	@if   [ -f loadtest/run.py ];      then $(UV) run python loadtest/run.py --url $(LB_URL) $(ARGS); \
	elif  [ -f loadtest/loadtest.py ]; then $(UV) run python loadtest/loadtest.py --url $(LB_URL) $(ARGS); \
	elif  [ -f loadtest/main.py ];     then $(UV) run python loadtest/main.py --url $(LB_URL) $(ARGS); \
	else printf "Script de carga ainda não existe em loadtest/\n"; exit 1; fi

# GRUPO: Nuvem (AWS)

# --- AWS Academy Sandbox -----------------------------------------------------
# A sandbox nao libera ElastiCache, ECR nem criacao de IAM; usa-se
# infra/terraform-sandbox/, e o ciclo saudavel e subir e destruir a cada sessao.

sandbox-status: scripts-exec ## Sandbox: credenciais, instance profile e o que esta no ar
	./scripts/sandbox.sh status

sandbox-up: scripts-exec ## Sandbox: provisiona e espera os nos ficarem saudaveis
	./scripts/sandbox.sh subir

sandbox-down: scripts-exec ## Sandbox: destroi tudo (RODE SEMPRE ao terminar)
	./scripts/sandbox.sh descer

sandbox-urls: scripts-exec ## Sandbox: reimprime os enderecos do ambiente
	./scripts/sandbox.sh urls

tf-init: ## terraform init em infra/terraform
	cd $(TF_DIR) && terraform init

tf-plan: ## terraform plan — revise ANTES de aplicar (custo!)
	cd $(TF_DIR) && terraform plan

tf-apply: ## terraform apply — provisiona VPC, ALB, ASG, Redis, DynamoDB, ECR
	cd $(TF_DIR) && terraform apply

tf-destroy: ## terraform destroy direto (prefira 'make teardown')
	cd $(TF_DIR) && terraform destroy

build-push: scripts-exec ## Constrói a imagem e envia para o Amazon ECR
	./scripts/build_push.sh $(ARGS)

deploy: scripts-exec ## Build + push + instance refresh do ASG + verificação
	./scripts/deploy.sh $(ARGS)

teardown: scripts-exec ## Destrói TODA a infraestrutura AWS (com confirmação)
	./scripts/teardown.sh $(ARGS)

# GRUPO: Manutenção

clean: ## Remove caches de build, teste e cobertura
	@find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache .coverage htmlcov dist build *.egg-info
	@printf "$(VERDE)Caches removidos.$(FIM)\n"
