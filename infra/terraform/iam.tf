# ---------------------------------------------------------------------------
# iam.tf — identidade dos nós, permissões mínimas e segredo JWT
#
# Regra que orienta este arquivo: NENHUMA credencial de longa duração existe no
# sistema. Não há access key na imagem, no user_data ou em variável de ambiente.
# A instância assume uma role via instance profile e recebe credenciais
# temporárias, rotacionadas pela própria AWS.
#
# Cada permissão abaixo existe porque uma linha específica do código a exige, e
# está limitada ao ARN exato do recurso. Não há `Action: "*"` nem
# `Resource: "*"` sem justificativa escrita.
# ---------------------------------------------------------------------------

# ===========================================================================
# Segredo JWT no SSM Parameter Store
# ===========================================================================

# SecureString: criptografado em repouso com a chave gerenciada `aws/ssm`.
#
# Por que Parameter Store e não Secrets Manager: o Parameter Store é gratuito no
# tier padrão; o Secrets Manager custa US$ 0,40/segredo/mês. Para um segredo
# estático, sem rotação automática, o Parameter Store entrega a mesma
# propriedade que importa aqui — o valor sai da IaC e passa a ser buscado em
# runtime pela identidade da instância.
#
# ATENÇÃO: o valor fica em texto claro no arquivo de estado local
# (terraform.tfstate). É por isso que o .gitignore do projeto exclui o estado, e
# a razão adicional para `terraform destroy` depois da apresentação.
resource "aws_ssm_parameter" "jwt_secret" {
  name        = local.jwt_parameter_name
  description = "Segredo HS256 de assinatura dos JWT do SalaViva"
  type        = "SecureString"
  value       = var.jwt_secret

  tags = {
    Name = "${local.name_prefix}-jwt-secret"
  }
}

# ===========================================================================
# Role assumida pelas instâncias EC2
# ===========================================================================

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    sid     = "PermiteEC2AssumirEstaRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${local.name_prefix}-node-role"
  description        = "Identidade dos nos SalaViva (DynamoDB, ECR, SSM, CloudWatch)"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json

  tags = {
    Name = "${local.name_prefix}-node-role"
  }
}

resource "aws_iam_instance_profile" "app" {
  name = "${local.name_prefix}-node-profile"
  role = aws_iam_role.app.name

  tags = {
    Name = "${local.name_prefix}-node-profile"
  }
}

# ===========================================================================
# Política de menor privilégio
# ===========================================================================

data "aws_iam_policy_document" "app" {

  # --- DynamoDB ------------------------------------------------------------
  # Exatamente as três operações que o código executa, nas duas tabelas:
  #   PutItem      → dynamo_repository.append()      (persistência da mensagem)
  #   Query        → dynamo_repository.backlog()     (replay por last_seq)
  #   DescribeTable→ dynamo_repository.healthy()     (checagem do /readyz)
  #
  # Ausentes de propósito: DeleteItem, UpdateItem e Scan. O histórico é
  # append-only por desenho — uma mensagem entregue não é reescrita nem apagada,
  # e a expiração fica por conta do TTL, que é executado pelo serviço e não
  # precisa de permissão do cliente. Se o código um dia tentar apagar, falha
  # com AccessDenied em vez de corromper o histórico silenciosamente.
  statement {
    sid    = "HistoricoDeMensagens"
    effect = "Allow"

    actions = [
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:DescribeTable",
    ]

    resources = [
      aws_dynamodb_table.messages.arn,
      aws_dynamodb_table.rooms.arn,
    ]
  }

  # --- ECR: token de autenticação -----------------------------------------
  # `ecr:GetAuthorizationToken` é uma das poucas ações da AWS que NÃO aceitam
  # restrição por recurso — ela opera sobre o registro da conta, não sobre um
  # repositório. O `Resource = "*"` aqui é imposto pela API, não uma folga
  # nossa; o que ela concede é apenas um token, e o que se pode fazer com o
  # token está limitado pela declaração seguinte.
  statement {
    sid       = "EcrTokenDeAutenticacao"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # --- ECR: pull do repositório do projeto --------------------------------
  # Somente leitura, e somente NESTE repositório. Sem `ecr:PutImage`: um nó
  # comprometido não consegue publicar uma imagem maliciosa que as próximas
  # instâncias baixariam. Publicar é papel da máquina de quem faz o deploy.
  statement {
    sid    = "EcrPullDaImagemDoProjeto"
    effect = "Allow"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]

    resources = [aws_ecr_repository.app.arn]
  }

  # --- SSM: leitura do segredo JWT ----------------------------------------
  # Um único parâmetro, nomeado. Não é `/salaviva/*`: o nó não tem motivo para
  # ler nenhum outro parâmetro, nem hoje nem quando outros forem criados.
  statement {
    sid    = "LeituraDoSegredoJwt"
    effect = "Allow"

    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
    ]

    resources = [aws_ssm_parameter.jwt_secret.arn]
  }

  # --- KMS: decifrar o SecureString ---------------------------------------
  # `--with-decryption` exige kms:Decrypt sobre a chave gerenciada `aws/ssm`.
  #
  # O `Resource = "*"` é restringido pela condição `kms:ViaService`: a permissão
  # só vale quando a chamada chega ao KMS ATRAVÉS do SSM desta região. A
  # instância não consegue usar kms:Decrypt diretamente para nada.
  #
  # A alternativa — descobrir o ARN da chave `alias/aws/ssm` com um data source
  # — foi descartada porque essa chave é criada pela AWS na primeira vez que um
  # SecureString é usado na conta: em uma conta nova o data source falharia no
  # `plan`, antes de o parâmetro existir.
  statement {
    sid       = "DecifrarSecureStringViaSsm"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }

  # --- CloudWatch Logs -----------------------------------------------------
  # O driver `awslogs` do Docker precisa criar um stream por instância e
  # escrever nele. O grupo já é criado pelo Terraform, então `logs:CreateLogGroup`
  # NÃO é concedido — a instância não pode inventar grupos de log fora do
  # espaço do projeto (nem gerar custo de retenção não planejado).
  statement {
    sid    = "LogsDoContainer"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["${aws_cloudwatch_log_group.app.arn}:*"]
  }
}

resource "aws_iam_policy" "app" {
  name        = "${local.name_prefix}-node-policy"
  description = "Permissoes minimas dos nos SalaViva"
  policy      = data.aws_iam_policy_document.app.json

  tags = {
    Name = "${local.name_prefix}-node-policy"
  }
}

resource "aws_iam_role_policy_attachment" "app" {
  role       = aws_iam_role.app.name
  policy_arn = aws_iam_policy.app.arn
}

# --- Acesso administrativo opcional ----------------------------------------
# Session Manager permite abrir shell na instância sem SSH, sem porta 22 e sem
# chave — melhor que SSH sob todos os aspectos, exceto um: a policy gerenciada
# da AWS é ampla, e todo o resto deste arquivo é escrito ação a ação. Por isso
# fica desligada por padrão e a decisão de ligá-la é explícita.
resource "aws_iam_role_policy_attachment" "ssm_session_manager" {
  count = var.enable_ssm_session_manager ? 1 : 0

  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
