# =============================================================================
# Rede
#
# Duas subredes públicas, uma por AZ. Não há subrede privada nesta variante, e
# a razão é diferente da que vale na infraestrutura de referência:
#
#   - Na referência, o ElastiCache é gerenciado e vive numa subrede privada sem
#     rota para a internet, porque a AWS o provisiona por dentro.
#   - Aqui o Redis roda num EC2 que precisa **baixar a imagem Docker** no boot.
#     Sem NAT Gateway (US$ ~32/mês, inviável nos US$ 20 da sandbox), a única
#     forma de a instância alcançar o Docker Hub é ter IP público.
#
# O isolamento, portanto, não vem da topologia — vem do Security Group: a porta
# 6379 só aceita tráfego do grupo das aplicações. Ter IP público não significa
# estar acessível; significa ter rota de saída.
# =============================================================================

resource "aws_vpc" "principal" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # necessário para resolução dentro da VPC

  tags = { Name = "${var.nome}-vpc" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.principal.id
  tags   = { Name = "${var.nome}-igw" }
}

# Duas AZs: o ALB exige no mínimo duas para ser criado, e é o que permite ao
# Auto Scaling recriar a instância derrubada em outra zona.
resource "aws_subnet" "publica" {
  count = 2

  vpc_id                  = aws_vpc.principal.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.disponiveis.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${var.nome}-publica-${count.index + 1}" }
}

resource "aws_route_table" "publica" {
  vpc_id = aws_vpc.principal.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = { Name = "${var.nome}-rt-publica" }
}

resource "aws_route_table_association" "publica" {
  count = length(aws_subnet.publica)

  subnet_id      = aws_subnet.publica[count.index].id
  route_table_id = aws_route_table.publica.id
}
