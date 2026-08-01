# =============================================================================
# Security Groups encadeados
#
#   internet ──80──► sg_alb ──8000──► sg_app ──6379──► sg_redis
#
# Cada elo só aceita tráfego do elo anterior, referenciado por ID de grupo e não
# por faixa de IP. A consequência prática: mesmo que uma instância ganhe IP
# público (e todas ganham, ver network.tf), a porta da aplicação e a do Redis
# permanecem inalcançáveis a partir da internet.
#
# Isso é o que responde à pergunta "mas o Redis não está exposto?" — não está.
# Ele tem rota de saída para baixar a imagem, e nenhuma rota de entrada exceto
# a partir dos nós da aplicação.
# =============================================================================

# --- Borda -------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${var.nome}-sg-alb"
  description = "Borda publica: unico ponto que aceita trafego da internet"
  vpc_id      = aws_vpc.principal.id

  tags = { Name = "${var.nome}-sg-alb" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP da internet"

  cidr_ipv4   = "0.0.0.0/0"
  from_port   = 80
  to_port     = 80
  ip_protocol = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_saida" {
  security_group_id = aws_security_group.alb.id
  description       = "ALB alcanca os alvos"

  cidr_ipv4   = "0.0.0.0/0"
  ip_protocol = "-1"
}

# --- Aplicação ---------------------------------------------------------------

resource "aws_security_group" "app" {
  name        = "${var.nome}-sg-app"
  description = "Nos da aplicacao: aceitam 8000 apenas do ALB"
  vpc_id      = aws_vpc.principal.id

  tags = { Name = "${var.nome}-sg-app" }
}

resource "aws_vpc_security_group_ingress_rule" "app_do_alb" {
  security_group_id = aws_security_group.app.id
  description       = "Porta da aplicacao, somente a partir do ALB"

  # Referência por ID do grupo, não por CIDR: quando o ALB troca de IP — e ele
  # troca —, a regra continua correta sem nenhuma alteração.
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "app_ssh" {
  count = var.ssh_cidr == "" ? 0 : 1

  security_group_id = aws_security_group.app.id
  description       = "SSH para depuracao (opcional)"

  cidr_ipv4   = var.ssh_cidr
  from_port   = 22
  to_port     = 22
  ip_protocol = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_saida" {
  security_group_id = aws_security_group.app.id
  description       = "Saida para GitHub, Docker Hub, DynamoDB e o Redis"

  cidr_ipv4   = "0.0.0.0/0"
  ip_protocol = "-1"
}

# --- Redis -------------------------------------------------------------------

resource "aws_security_group" "redis" {
  name        = "${var.nome}-sg-redis"
  description = "Redis: aceita 6379 apenas dos nos da aplicacao"
  vpc_id      = aws_vpc.principal.id

  tags = { Name = "${var.nome}-sg-redis" }
}

resource "aws_vpc_security_group_ingress_rule" "redis_do_app" {
  security_group_id = aws_security_group.redis.id
  description       = "Redis somente a partir dos nos da aplicacao"

  referenced_security_group_id = aws_security_group.app.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "redis_ssh" {
  count = var.ssh_cidr == "" ? 0 : 1

  security_group_id = aws_security_group.redis.id
  description       = "SSH para depuracao (opcional)"

  cidr_ipv4   = var.ssh_cidr
  from_port   = 22
  to_port     = 22
  ip_protocol = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "redis_saida" {
  security_group_id = aws_security_group.redis.id
  description       = "Saida para baixar a imagem do Redis no boot"

  cidr_ipv4   = "0.0.0.0/0"
  ip_protocol = "-1"
}
