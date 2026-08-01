# =============================================================================
# SalaViva — variante para AWS Academy Sandbox
#
# POR QUE ESTA PASTA EXISTE
#
# `infra/terraform/` é a infraestrutura de referência, e é ela que o SDD
# documenta: ElastiCache gerenciado, ECR, IAM de menor privilégio criado sob
# medida e segredo em SSM SecureString.
#
# A sandbox da AWS Academy não permite nada disso:
#
#   | Recurso da referência        | Situação na sandbox                     |
#   |------------------------------|-----------------------------------------|
#   | aws_elasticache_cluster      | ElastiCache não liberado                |
#   | aws_ecr_repository           | ECR não liberado                        |
#   | aws_iam_role / _policy       | IAM estritamente read-only              |
#   | aws_ssm_parameter SecureString | KMS só permite listagem               |
#
# Esta variante troca cada um desses por um equivalente permitido, **sem alterar
# o comportamento do sistema**: continua sendo Pub/Sub para difusão, `INCR` para
# ordem total e ZSET para presença. O que muda é quem hospeda o Redis e de onde
# vem a imagem — não a arquitetura.
#
# As divergências e o motivo de cada uma estão em README.md desta pasta.
# =============================================================================

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  # A sandbox opera exclusivamente em us-east-1.
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "SalaViva"
      ManagedBy = "Terraform"
      Ambiente  = "academy-sandbox"
    }
  }
}

# AZs realmente disponíveis para a conta. Fixar "us-east-1a/b" no código
# quebraria caso a sandbox aloque a conta em outro par de zonas — o que
# acontece, porque a AWS distribui contas entre AZs físicas diferentes.
data "aws_availability_zones" "disponiveis" {
  state = "available"
}

# Amazon Linux 2023 mais recente, via parâmetro público do SSM. Fixar um AMI ID
# quebraria em poucas semanas, quando a AWS publicasse uma nova imagem.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}
