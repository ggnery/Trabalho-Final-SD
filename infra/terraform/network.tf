# ---------------------------------------------------------------------------
# network.tf — VPC, subredes, roteamento
#
# Desenho: 2 AZs × 2 camadas.
#
#   AZ us-east-1a                         AZ us-east-1b
#   ┌──────────────────────────┐          ┌──────────────────────────┐
#   │ pública  10.20.1.0/24    │          │ pública  10.20.2.0/24    │
#   │   ALB + EC2 (ASG)        │          │   ALB + EC2 (ASG)        │
#   │   rota 0.0.0.0/0 → IGW   │          │   rota 0.0.0.0/0 → IGW   │
#   ├──────────────────────────┤          ├──────────────────────────┤
#   │ privada 10.20.101.0/24   │          │ privada 10.20.102.0/24   │
#   │   ElastiCache            │          │   ElastiCache            │
#   │   SEM rota para internet │          │   SEM rota para internet │
#   └──────────────────────────┘          └──────────────────────────┘
#
# POR QUE A EC2 FICA NA SUBREDE PÚBLICA (ADR-006):
# a prática de produção seria EC2 em subrede privada + NAT Gateway. O NAT custa
# ~US$ 32/mês por AZ e NÃO é coberto pelo Free Tier — seria, sozinho, o maior
# item de custo do projeto, maior que todo o resto somado. A proteção efetiva
# aqui vem do Security Group encadeado (ver security.tf): a porta 8000 só aceita
# tráfego do SG do ALB, então a aplicação é inalcançável da internet mesmo com
# IP público na instância. O ElastiCache, esse sim, permanece em subrede sem
# rota de saída. O desvio é consciente e está declarado no SDD.
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr

  # DNS interno é obrigatório aqui: o endpoint do ElastiCache é entregue como
  # nome DNS (…cache.amazonaws.com), não como IP. Sem resolução na VPC, o
  # SALAVIVA_REDIS_URL montado no user_data não resolveria.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${local.name_prefix}-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-igw"
  }
}

# --- Subredes públicas: ALB e nós de aplicação -----------------------------

resource "aws_subnet" "public" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = var.public_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  # IP público automático: sem NAT, é o único caminho das instâncias para o ECR
  # (pull da imagem), o SSM (leitura do segredo) e o CloudWatch (logs).
  map_public_ip_on_launch = true

  tags = {
    Name = "${local.name_prefix}-public-${var.availability_zones[count.index]}"
    Tier = "public"
  }
}

# --- Subredes privadas: apenas ElastiCache ---------------------------------

resource "aws_subnet" "private" {
  count = length(var.availability_zones)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.private_subnet_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.name_prefix}-private-${var.availability_zones[count.index]}"
    Tier = "private"
  }
}

# --- Roteamento ------------------------------------------------------------

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${local.name_prefix}-rt-public"
  }
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Tabela privada declarada SEM nenhuma rota além da `local` implícita da VPC.
# Isso é o isolamento do Redis no nível de roteamento: mesmo que alguém afrouxe
# o Security Group por engano, não existe caminho de/para a internet a partir
# desta subrede. Duas barreiras independentes, não uma.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-rt-private"
  }
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# --- Gateway Endpoint do DynamoDB ------------------------------------------
# Sem ele, o tráfego EC2 → DynamoDB sairia pelo IGW e voltaria pela internet
# pública da AWS. Com ele, o tráfego é roteado dentro da rede da AWS.
#
# Vale a pena porque Gateway Endpoints (S3 e DynamoDB) são GRATUITOS — ao
# contrário dos Interface Endpoints (PrivateLink), que custam ~US$ 7/mês cada e
# por isso NÃO usamos para ECR/SSM/CloudWatch. É a única parte do isolamento de
# saída que cabe no orçamento do projeto.
resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]

  tags = {
    Name = "${local.name_prefix}-vpce-dynamodb"
  }
}
