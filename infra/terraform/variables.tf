# ---------------------------------------------------------------------------
# variables.tf — parâmetros de entrada
#
# Toda variável tem default utilizável, EXCETO `jwt_secret`: um segredo com
# valor padrão é um segredo vazado. Ele é obrigatório de propósito, para que
# ninguém suba o ambiente com a chave de assinatura de exemplo.
# ---------------------------------------------------------------------------

# --- Identificação ---------------------------------------------------------

variable "project_name" {
  description = "Prefixo de nome de todos os recursos. Aparece no console da AWS."
  type        = string
  default     = "salaviva"

  validation {
    # Nomes de ALB/Target Group aceitam apenas alfanuméricos e hífen, e o nome
    # completo tem teto de 32 caracteres. Validar aqui produz um erro legível no
    # `plan` em vez de um erro críptico da API da AWS no meio do `apply`.
    condition     = can(regex("^[a-z][a-z0-9-]{1,14}$", var.project_name))
    error_message = "project_name deve ter 2-15 caracteres minúsculos, começar com letra e conter apenas [a-z0-9-]."
  }
}

variable "environment" {
  description = "Ambiente lógico (demo, prod). Compõe o nome dos recursos e vai para SALAVIVA_ENVIRONMENT."
  type        = string
  default     = "demo"

  validation {
    condition     = can(regex("^[a-z0-9-]{2,10}$", var.environment))
    error_message = "environment deve ter 2-10 caracteres em [a-z0-9-]."
  }
}

# --- Região e rede ---------------------------------------------------------

variable "aws_region" {
  description = "Região AWS. us-east-1 tem a maior cobertura de Free Tier (ADR/Requirements)."
  type        = string
  default     = "us-east-1"
}

variable "availability_zones" {
  description = <<-EOT
    AZs usadas. São exatamente duas por dois motivos:
    (1) o Application Load Balancer exige no mínimo duas AZs para ser criado;
    (2) com min_size = 2 no ASG, uma AZ inteira pode cair e o chat continua.
  EOT
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]

  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "Este projeto assume exatamente 2 AZs (requisito do ALB e do desenho de subredes)."
  }
}

variable "vpc_cidr" {
  description = "Bloco CIDR da VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDRs das subredes públicas (ALB + EC2). Uma por AZ."
  type        = list(string)
  default     = ["10.20.1.0/24", "10.20.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = <<-EOT
    CIDRs das subredes privadas. Hospedam SOMENTE o ElastiCache.
    Não têm rota para o Internet Gateway — o Redis é inalcançável de fora da VPC
    por construção de roteamento, antes mesmo de qualquer Security Group.
  EOT
  type        = list(string)
  default     = ["10.20.101.0/24", "10.20.102.0/24"]
}

# --- Acesso administrativo -------------------------------------------------

variable "allowed_ssh_cidr" {
  description = <<-EOT
    CIDR autorizado a abrir SSH (22) nas instâncias. Vazio = regra não criada,
    que é o padrão seguro.
    Use o SEU IP em /32 (ex.: "203.0.113.42/32") apenas se precisar depurar um
    nó ao vivo durante a apresentação. Nunca 0.0.0.0/0.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.allowed_ssh_cidr == "" || can(cidrnetmask(var.allowed_ssh_cidr))
    error_message = "allowed_ssh_cidr deve ser vazio ou um CIDR válido (ex.: 203.0.113.42/32)."
  }
}

variable "key_pair_name" {
  description = "Nome de um EC2 Key Pair já existente, para SSH. Vazio = instâncias sem chave."
  type        = string
  default     = ""
}

variable "enable_ssm_session_manager" {
  description = <<-EOT
    Anexa a policy gerenciada AmazonSSMManagedInstanceCore, habilitando shell via
    Session Manager (sem SSH, sem porta aberta, sem chave).

    Padrão `false` por coerência com o requisito de MENOR PRIVILÉGIO: essa policy
    é ampla (ssm:*, ec2messages:*, s3:GetObject em buckets da AWS) e o resto do
    IAM deste projeto é escrito recurso a recurso. Ligue apenas se for depurar.
  EOT
  type        = bool
  default     = false
}

# --- Camada de aplicação ---------------------------------------------------

variable "instance_type" {
  description = "Tipo das instâncias de aplicação. t3.micro é o alvo do Free Tier."
  type        = string
  default     = "t3.micro"
}

variable "asg_min_size" {
  description = "Mínimo do ASG. 2 garante que derrubar um nó na demo (EC3) não zere o serviço."
  type        = number
  default     = 2
}

variable "asg_desired_capacity" {
  description = "Capacidade desejada. 3 nós tornam visível a distribuição no /dashboard."
  type        = number
  default     = 3
}

variable "asg_max_size" {
  description = "Teto do ASG. 4 limita o custo mesmo que algo entre em laço de substituição."
  type        = number
  default     = 4
}

variable "image_tag" {
  description = <<-EOT
    Tag da imagem no ECR que as instâncias baixam no boot.
    `latest` é conveniente para a demo (basta um push + instance refresh), mas em
    produção usaríamos uma tag imutável por commit — com `latest` não se sabe,
    olhando o ASG, qual código está rodando.
  EOT
  type        = string
  default     = "latest"
}

variable "app_port" {
  description = "Porta HTTP/WebSocket da aplicação. Fixada em 8000 pelo backend."
  type        = number
  default     = 8000
}

variable "log_level" {
  description = "SALAVIVA_LOG_LEVEL das instâncias."
  type        = string
  default     = "info"
}

variable "log_retention_days" {
  description = "Retenção dos logs no CloudWatch. 7 dias mantém o custo em zero prático."
  type        = number
  default     = 7
}

# --- Segurança / segredo ---------------------------------------------------

variable "jwt_secret" {
  description = <<-EOT
    Segredo HS256 usado para assinar e validar os JWT. SEM DEFAULT de propósito.

    Vai para o SSM Parameter Store como SecureString e é lido pelo nó no boot
    através do instance profile — nunca fica na imagem Docker nem no user_data.
    Gere com: openssl rand -hex 32
  EOT
  type        = string
  sensitive   = true

  validation {
    # Sem espaços: o valor é escrito em um arquivo de ambiente consumido por
    # `docker run --env-file`, que não faz parsing de aspas.
    condition     = length(var.jwt_secret) >= 32 && can(regex("^\\S+$", var.jwt_secret))
    error_message = "jwt_secret deve ter no mínimo 32 caracteres e nenhum espaço em branco."
  }
}

variable "certificate_arn" {
  description = <<-EOT
    ARN de um certificado ACM para habilitar o listener HTTPS (443) e, portanto,
    WSS no cliente.

    Vazio por padrão: a demo roda em HTTP/WS porque não temos domínio próprio, e
    o ACM só emite certificado para domínio que se controla (o DNS do ALB não
    serve). O caminho para HTTPS está pronto — basta informar o ARN.
  EOT
  type        = string
  default     = ""
}

# --- Dados -----------------------------------------------------------------

variable "redis_node_type" {
  description = "Tipo do nó ElastiCache. cache.t3.micro é o elegível ao Free Tier."
  type        = string
  default     = "cache.t3.micro"
}

variable "redis_engine_version" {
  description = "Versão do Redis. 7.x traz o Pub/Sub e os comandos usados (INCR, ZSET)."
  type        = string
  default     = "7.1"
}

variable "messages_table_name" {
  description = "Tabela DynamoDB do histórico. Deve bater com SALAVIVA_MESSAGES_TABLE."
  type        = string
  default     = "salaviva_messages"
}

variable "rooms_table_name" {
  description = "Tabela DynamoDB de salas. Deve bater com SALAVIVA_ROOMS_TABLE."
  type        = string
  default     = "salaviva_rooms"
}

variable "persistence_enabled" {
  description = <<-EOT
    Liga a persistência no DynamoDB (SALAVIVA_PERSISTENCE_ENABLED).
    `false` mantém o chat em tempo real funcionando e desliga apenas o replay de
    histórico (ADR-008) — útil como plano B se o DynamoDB falhar na hora da demo.
  EOT
  type        = bool
  default     = true
}

# --- Valores derivados -----------------------------------------------------

locals {
  # Prefixo único de nome. Concentrado aqui para que trocar `environment` gere um
  # ambiente paralelo completo, sem colisão de nomes com o existente.
  name_prefix = "${var.project_name}-${var.environment}"

  # Caminho do segredo no Parameter Store. Usado em três lugares (recurso,
  # policy IAM e user_data), então mora em um único ponto.
  jwt_parameter_name = "/${var.project_name}/${var.environment}/jwt_secret"

  log_group_name = "/${var.project_name}/${var.environment}/app"
}
