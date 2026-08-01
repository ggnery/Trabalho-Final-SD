# =============================================================================
# Variáveis — variante sandbox
# =============================================================================

variable "aws_region" {
  description = "Região. A sandbox da AWS Academy só opera em us-east-1."
  type        = string
  default     = "us-east-1"
}

variable "nome" {
  description = "Prefixo dos nomes de recurso."
  type        = string
  default     = "salaviva"
}

# --- Aplicação ---------------------------------------------------------------

variable "repositorio_git" {
  description = <<-EOT
    Repositório público de onde as instâncias clonam e constroem a imagem.

    Substitui o ECR, que não está liberado na sandbox. Precisa ser PÚBLICO: a
    instância clona sem credencial nenhuma. Se o seu repositório for privado,
    torne-o público ou use `imagem_docker` (ver abaixo).
  EOT
  type        = string
  default     = "https://github.com/ggnery/Trabalho-Final-SD.git"
}

variable "branch_git" {
  description = "Branch a clonar."
  type        = string
  default     = "main"
}

variable "imagem_docker" {
  description = <<-EOT
    Alternativa ao build na instância: imagem já publicada (ex.: Docker Hub).

    Se preenchida, o `user_data` faz `docker pull` em vez de `git clone` +
    `docker build`. Reduz o tempo de boot de ~4 min para ~40 s, o que importa na
    demonstração de falha — é o intervalo entre derrubar um nó e o substituto
    entrar em serviço. Deixe vazio para construir na instância.
  EOT
  type        = string
  default     = ""
}

variable "jwt_secret" {
  description = <<-EOT
    Segredo de assinatura dos tokens JWT. Gere com: openssl rand -base64 32

    DIVERGÊNCIA CONSCIENTE: na infraestrutura de referência este valor vive em
    SSM Parameter Store como SecureString, lido no boot pela instance profile.
    A sandbox só concede listagem no KMS, então o SecureString não pode ser
    criado nem decifrado — aqui o segredo vai no `user_data`, que fica visível
    para quem tiver `ec2:DescribeInstanceAttribute` na conta.

    É aceitável no contexto acadêmico e está declarado no README. Em produção,
    não seria.
  EOT
  type        = string
  sensitive   = true
}

# --- Dimensionamento ---------------------------------------------------------

variable "tipo_instancia" {
  description = "A sandbox permite apenas t2/t3 nano, micro, small e medium."
  type        = string
  default     = "t3.micro"

  validation {
    condition     = can(regex("^t[23]\\.(nano|micro|small|medium)$", var.tipo_instancia))
    error_message = "A sandbox só permite t2/t3 nas famílias nano, micro, small e medium."
  }
}

variable "nos_min" {
  description = "Mínimo de nós no Auto Scaling Group."
  type        = number
  default     = 2
}

variable "nos_desejados" {
  description = <<-EOT
    Quantidade desejada de nós.

    Três é o número da demonstração: com dois, derrubar um deixa metade do
    cluster fora e a plateia não vê a diferença entre "tolerou a falha" e
    "sobrou o outro". Com três, a perda de um é visivelmente parcial.

    Some 1 (o Redis): o total de instâncias é `nos_desejados + 1`, contra o
    limite de 9 da sandbox.
  EOT
  type        = number
  default     = 3
}

variable "nos_max" {
  description = "Teto do Auto Scaling Group. Trava de orçamento, não limite arquitetural."
  type        = number
  default     = 4
}

# --- Acesso e persistência ---------------------------------------------------

variable "instance_profile" {
  description = <<-EOT
    Nome de uma instance profile JÁ EXISTENTE a anexar às instâncias.

    A sandbox provê `LabInstanceProfile` (vinculada à `LabRole`) e **não permite
    criar novas** — IAM é read-only. É essa profile que dá às instâncias acesso
    ao DynamoDB.

    Deixe vazio ("") se a sua sandbox não tiver essa profile. Nesse caso o
    Terraform desliga a persistência automaticamente: o chat continua
    funcionando em tempo real (o Pub/Sub não depende do DynamoDB), mas o replay
    de histórico na reconexão fica indisponível — e a prova de "zero mensagens
    perdidas" passa a depender só da continuidade do `seq`.

    Confira o nome com:  aws iam list-instance-profiles --query 'InstanceProfiles[].InstanceProfileName'
  EOT
  type        = string
  default     = "LabInstanceProfile"
}

variable "persistencia_habilitada" {
  description = <<-EOT
    Grava o histórico no DynamoDB. Exige `instance_profile` preenchida.

    Se `instance_profile` estiver vazia, este valor é ignorado e a persistência
    é desligada — sem a role, a instância não tem como autenticar no DynamoDB.
  EOT
  type        = bool
  default     = true
}

variable "ssh_cidr" {
  description = <<-EOT
    CIDR autorizado a abrir SSH (porta 22) nas instâncias.

    Vazio = nenhuma porta SSH liberada, que é o padrão. A sandbox oferece
    EC2 Instance Connect e SSM Session Manager pelo console, então acesso por
    SSH normalmente não é necessário. Se for depurar, use seu IP com /32 —
    nunca 0.0.0.0/0.
  EOT
  type        = string
  default     = ""
}

# --- Rede --------------------------------------------------------------------

variable "vpc_cidr" {
  description = "Bloco CIDR da VPC."
  type        = string
  default     = "10.30.0.0/16"
}
