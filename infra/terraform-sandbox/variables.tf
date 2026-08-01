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

    IAM é read-only na sandbox: não dá para criar a role de menor privilégio que
    a infraestrutura de referência cria. Só resta reaproveitar uma das que já
    existem, e o que existe varia conforme o tipo de sandbox:

      - Learner Lab           -> LabInstanceProfile
      - Cloud Architecting    -> EMR_EC2_DefaultRole, myS3Role

    O padrão é `EMR_EC2_DefaultRole` porque é a única, entre as disponíveis na
    Cloud Architecting, cuja policy (`AmazonElasticMapReduceforEC2Role`) concede
    `dynamodb:*` — e sem acesso ao DynamoDB o replay de histórico não funciona.

    DIVERGÊNCIA DECLARADA: usar a role do EMR num chat é semanticamente errado e
    concede muito mais do que o necessário (S3, Kinesis, SQS além do DynamoDB).
    É o oposto do menor privilégio. A infraestrutura de referência
    (`infra/terraform/`) cria uma policy com exatamente três ações no DynamoDB;
    aqui isso é impossível, porque criar policies exige permissão que a sandbox
    não concede. Se perguntarem na banca, esta é a resposta — e ela é sobre a
    restrição do ambiente, não sobre desleixo no projeto.

    Descubra o que a SUA sandbox oferece com:
      aws iam list-instance-profiles --query 'InstanceProfiles[].InstanceProfileName' --output text

    O PADRÃO É VAZIO porque a sandbox Cloud Architecting nega `iam:PassRole`:
    mesmo existindo profiles, o Auto Scaling recusa o Launch Template com
    "AccessDenied: You are not authorized to use launch template". Com
    `dynamodb_local = true` (o padrão) isso não é problema — o histórico não
    precisa de credencial nenhuma.

    Só preencha se a sua sandbox permitir PassRole E você quiser usar o
    DynamoDB gerenciado.
  EOT
  type        = string
  default     = ""
}

variable "dynamodb_local" {
  description = <<-EOT
    Roda o DynamoDB Local em contêiner, ao lado do Redis, em vez de usar o
    serviço gerenciado.

    POR QUE É O PADRÃO NA SANDBOX

    Escrever no DynamoDB gerenciado a partir de uma EC2 exige uma instance
    profile. A sandbox Cloud Architecting nega `iam:PassRole`, então o Auto
    Scaling recusa qualquer Launch Template que tenha uma profile anexada — não
    há como dar credencial às instâncias.

    Restavam duas saídas: desligar a persistência, ou hospedar o armazenamento.
    Desligar custaria caro na demonstração: sem histórico, o cliente que
    reconecta após a queda de um nó não recebe o backlog, e a prova de "zero
    mensagens perdidas" — o ponto central do critério EC3 — deixaria de ser
    demonstrável.

    O DynamoDB Local é a mesma API, com o mesmo modelo de chave
    `(room_id, seq)` e a mesma Query de replay. O que se perde é durabilidade
    real e escala; o que se preserva é exatamente o que a demonstração precisa
    provar. É o mesmo componente que o `docker-compose.yml` usa localmente.

    Defina `false` se a sua sandbox permitir PassRole — aí vale usar o serviço
    gerenciado, que é o que a infraestrutura de referência faz.
  EOT
  type        = bool
  default     = true
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
