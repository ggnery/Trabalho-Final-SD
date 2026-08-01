# ---------------------------------------------------------------------------
# ecr.tf — registro da imagem Docker dos nós
#
# POR QUE UM REGISTRO PRIVADO NA PRÓPRIA CONTA: a alternativa seria o Docker Hub
# público, que traria duas dores concretas. Primeiro, autenticação: um pull
# autenticado exigiria usuário e senha no user_data — exatamente a credencial de
# longa duração que a arquitetura evita. Com o ECR, a instância se autentica com
# a role do instance profile. Segundo, limite de taxa: pulls anônimos do Docker
# Hub são limitados por IP, e três instâncias subindo ao mesmo tempo durante um
# instance refresh podem esbarrar nisso — falhando justamente na hora da demo.
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "app" {
  name = local.name_prefix

  # MUTABLE porque a tag padrão é `latest` e o fluxo de demo é
  # "build → push → instance refresh". Com IMMUTABLE, o segundo push da mesma
  # tag seria rejeitado e cada correção exigiria uma tag nova.
  # Em produção o correto é o inverso: IMMUTABLE + tag por commit, para que
  # "qual código está rodando" tenha resposta única.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    # Scan básico de vulnerabilidades no push — sem custo. Aparece no console e
    # é um item concreto de segurança para citar na apresentação.
    scan_on_push = true
  }

  encryption_configuration {
    # AES256 com chave gerenciada pela AWS: criptografia em repouso sem os
    # US$ 1/mês de uma chave KMS própria.
    encryption_type = "AES256"
  }

  # CRÍTICO PARA O `terraform destroy`: por padrão, a AWS recusa apagar um
  # repositório que contenha imagens, e o destroy pós-apresentação falharia com
  # `RepositoryNotEmptyException` — deixando o repositório (e sua cobrança de
  # armazenamento) para trás sem que ninguém percebesse.
  force_delete = true

  tags = {
    Name = local.name_prefix
  }
}

# --- Política de ciclo de vida ---------------------------------------------
# Cada build empurra uma camada nova; sem expiração, o repositório cresce a cada
# deploy e o armazenamento (US$ 0,10/GB-mês) passa a ser cobrado por imagens que
# ninguém vai usar de novo. Cinco versões é o suficiente para voltar atrás em um
# deploy ruim durante a apresentação.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Manter apenas as 5 imagens mais recentes"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = {
          type = "expire"
        }
      },
    ]
  })
}
