# ---------------------------------------------------------------------------
# security.tf — Security Groups ENCADEADOS
#
#   internet ──80/443──▶ [sg_alb] ──8000──▶ [sg_app] ──6379──▶ [sg_redis]
#
# A propriedade que importa: cada regra de entrada aponta para o SECURITY GROUP
# de origem, não para um CIDR. Consequências práticas:
#
#  * A porta 8000 não é alcançável da internet, mesmo com a instância tendo IP
#    público — só o ALB consegue falar com ela. É isto que torna ADR-006
#    (EC2 em subrede pública, sem NAT) defensável.
#  * O Auto Scaling pode criar e destruir instâncias à vontade: a autorização é
#    por pertencimento ao grupo, não por endereço. Nenhuma regra precisa ser
#    reescrita quando um nó é derrubado na demo e outro nasce com IP novo.
#  * Nada no caminho depende de o Redis "confiar em um IP": ele confia em quem
#    está no sg_app, e ninguém entra no sg_app sem ser lançado pelo ASG.
#
# Usamos os recursos granulares `aws_vpc_security_group_(in|e)gress_rule` em vez
# de blocos inline. Além de ser a forma recomendada no provider 5.x, evita o
# ciclo de dependência clássico entre dois SGs que se referenciam mutuamente
# (sg_alb precisa de egress para sg_app, sg_app precisa de ingress de sg_alb).
# ---------------------------------------------------------------------------

# ===========================================================================
# 1) SG do Application Load Balancer — a única porta de entrada da internet
# ===========================================================================

resource "aws_security_group" "alb" {
  # `name_prefix` em vez de `name`: mudar a descrição de um SG força recriação
  # na AWS, e o par `create_before_destroy` + nome fixo colidiria consigo mesmo
  # ("já existe um SG com este nome"). Com prefixo, o substituto nasce com um
  # sufixo único, assume as referências e só então o antigo é removido. A tag
  # Name é o que identifica o grupo no console.
  name_prefix = "${local.name_prefix}-sg-alb-"
  description = "Borda: aceita HTTP/HTTPS da internet e fala apenas com os nos da aplicacao"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-sg-alb"
  }

  lifecycle {
    # O SG é referenciado pelo ALB; criar o substituto antes de destruir o
    # antigo evita o erro "resource in use" em mudanças de configuração.
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP/WS publico (demo sem dominio proprio)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

# 443 fica aberto mesmo sem listener HTTPS ativo: assim, informar
# `certificate_arn` habilita WSS sem exigir mudança de rede junto.
resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS/WSS publico (ativo quando certificate_arn for informado)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# Saída restrita ao destino real: o ALB não precisa falar com mais nada no
# mundo além da porta da aplicação nos nós. O padrão da AWS seria liberar toda
# a saída; aqui a regra é explícita e mínima.
resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Encaminhamento e health check para os nos, somente na porta da app"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = var.app_port
  to_port                      = var.app_port
}

# ===========================================================================
# 2) SG dos nós de aplicação (EC2 do ASG)
# ===========================================================================

resource "aws_security_group" "app" {
  name_prefix = "${local.name_prefix}-sg-app-"
  description = "Nos SalaViva: recebem trafego apenas do ALB"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-sg-app"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# O ELO CENTRAL DA CADEIA: a porta da aplicação aceita conexão apenas de quem
# está no SG do ALB. Este mesmo caminho serve o tráfego WebSocket dos clientes e
# o health check ativo em /readyz.
resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "Porta da aplicacao (HTTP + upgrade WebSocket) exclusivamente a partir do ALB"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.app_port
  to_port                      = var.app_port
}

# SSH: só existe se `allowed_ssh_cidr` for informado. `count` em vez de um CIDR
# padrão garante que o estado normal do ambiente é sem porta administrativa
# aberta — a exceção precisa ser pedida explicitamente.
resource "aws_vpc_security_group_ingress_rule" "app_ssh" {
  count = var.allowed_ssh_cidr == "" ? 0 : 1

  security_group_id = aws_security_group.app.id
  description       = "SSH administrativo, restrito ao CIDR informado"
  cidr_ipv4         = var.allowed_ssh_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

# Saída ampla, e o motivo é a ausência de NAT/PrivateLink: o nó precisa alcançar
# os endpoints públicos do ECR (pull da imagem), do SSM (segredo JWT) e do
# CloudWatch Logs. Fechar isso exigiria Interface Endpoints pagos (~US$ 7/mês
# cada, três deles) — fora do orçamento Free Tier. O tráfego para o DynamoDB já
# é o caso resolvido: sai pelo Gateway Endpoint gratuito (network.tf).
resource "aws_vpc_security_group_egress_rule" "app_egress" {
  security_group_id = aws_security_group.app.id
  description       = "Saida para ECR, SSM, CloudWatch e ElastiCache"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ===========================================================================
# 3) SG do ElastiCache Redis — a ponta da cadeia
# ===========================================================================

resource "aws_security_group" "redis" {
  name_prefix = "${local.name_prefix}-sg-redis-"
  description = "ElastiCache: aceita 6379 exclusivamente dos nos da aplicacao"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-sg-redis"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# O Redis concentra o que há de mais sensível na arquitetura: o sequenciador de
# ordem total (INCR), o canal Pub/Sub e a presença. Uma única regra de entrada,
# vinda de um único grupo. Nem o ALB nem a internet têm caminho até aqui.
resource "aws_vpc_security_group_ingress_rule" "redis_from_app" {
  security_group_id            = aws_security_group.redis.id
  description                  = "Pub/Sub, INCR e ZSET exclusivamente a partir dos nos da aplicacao"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
}

# Nenhuma regra de egress para o Redis, de propósito. Security Group é stateful:
# a resposta de uma conexão aceita volta sem precisar de autorização de saída.
# O que fica bloqueado é o Redis INICIAR conexões — que ele não tem motivo para
# fazer, e que é exatamente o movimento de um comprometimento.
