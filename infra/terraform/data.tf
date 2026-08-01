# ---------------------------------------------------------------------------
# data.tf — camada de dados: ElastiCache (Redis) e DynamoDB
#
# Dois armazenamentos com papéis deliberadamente distintos (data-stack.md):
#
#   Redis    → estado volátil e COORDENAÇÃO. Requisito: latência.
#              Pub/Sub chat:room:{id}, INCR chat:seq:{id}, ZSETs de presença e
#              de nós vivos. Se sumir, perde-se presença, não histórico.
#
#   DynamoDB → HISTÓRICO durável. Requisito: durabilidade.
#              (room_id, seq) → mensagem. Se sumir, perde-se o replay, não a
#              entrega em tempo real.
#
# Separar por requisito, e não por "banco principal + cache", é o que permite a
# demonstração de falha não perder mensagem: o `seq` (Redis) e o histórico
# (DynamoDB) vivem fora do nó que morre.
# ---------------------------------------------------------------------------

# ===========================================================================
# ElastiCache for Redis — broker Pub/Sub e sequenciador de ordem total
# ===========================================================================

# As subredes privadas entram aqui — é a única coisa que roda nelas. Como não
# há rota para o Internet Gateway, o Redis não tem endereço alcançável de fora
# da VPC nem caminho de saída.
resource "aws_elasticache_subnet_group" "redis" {
  name        = "${local.name_prefix}-cache-subnets"
  description = "Subredes privadas (sem rota para internet) do ElastiCache"
  subnet_ids  = [for s in aws_subnet.private : s.id]

  tags = {
    Name = "${local.name_prefix}-cache-subnets"
  }
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id = "${local.name_prefix}-redis"
  engine     = "redis"

  # Redis 7.x: o Pub/Sub e os comandos usados (INCR, ZADD/ZRANGEBYSCORE,
  # SET NX) existem desde versões antigas, mas 7.x é o que a AWS mantém com
  # correções — e evita depender de comportamento descontinuado.
  engine_version       = var.redis_engine_version
  parameter_group_name = "default.redis7"

  node_type = var.redis_node_type
  port      = 6379

  # ---------------------------------------------------------------------
  # NÓ ÚNICO — limitação assumida e declarada (ADR-002).
  #
  # É o ponto único de falha da arquitetura: sem Redis não há fan-out nem
  # sequenciador, e o chat para. A mitigação de produção seria um
  # `aws_elasticache_replication_group` Multi-AZ com réplica e failover
  # automático, o que dobra o custo e sai do Free Tier.
  #
  # A falha é COERENTE, não silenciosa: sem Redis o /readyz reprova, o ALB tira
  # os nós do pool e o cliente vê erro de conexão — em vez de o chat continuar
  # "funcionando" com ordem errada. Preferimos parar a entregar ordem incorreta.
  # ---------------------------------------------------------------------
  num_cache_nodes = 1

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  # Backup desligado: o conteúdo do Redis aqui é estado de coordenação
  # (presença, sequenciadores) que não faz sentido restaurar — restaurar um
  # `chat:seq` antigo REGREDIRIA a numeração e quebraria a ordem total. Além
  # disso, snapshot é armazenamento cobrado.
  snapshot_retention_limit = 0

  # Sem criptografia em trânsito, e note que nem seria configurável aqui: para
  # Redis, `transit_encryption_enabled` e `auth_token` pertencem ao recurso
  # `aws_elasticache_replication_group`, não a um cluster de nó único. Como o
  # tráfego está confinado a uma subrede sem rota de saída, atrás de um SG que
  # só aceita os nós da aplicação, a lacuna é aceitável. Em produção, o mesmo
  # movimento que traria a réplica Multi-AZ traria o TLS junto — a URL passaria
  # a ser `rediss://`.

  # Janela de manutenção em horário de baixo uso (UTC). Evita que a AWS reinicie
  # o nó no meio de uma apresentação diurna no Brasil.
  maintenance_window = "sun:07:00-sun:08:00"

  # Mudanças de parâmetro valem imediatamente em vez de esperar a janela — em um
  # ambiente de demonstração, ninguém quer descobrir amanhã se a alteração
  # funcionou.
  apply_immediately = true

  tags = {
    Name = "${local.name_prefix}-redis"
  }
}

# ===========================================================================
# DynamoDB — histórico durável e replay de reconexão
# ===========================================================================

resource "aws_dynamodb_table" "messages" {
  name = var.messages_table_name

  # ---------------------------------------------------------------------
  # CHAVE COMPOSTA (room_id, seq) — o desenho que faz o replay ser barato.
  #
  # `seq` é sort key, e não um atributo comum, porque assim a ORDEM TOTAL da
  # sala fica materializada no próprio índice. O replay de reconexão vira uma
  # única Query:
  #
  #   room_id = :r AND seq > :last_seq
  #
  # já devolvida em ordem crescente pelo índice — sem Scan, sem ordenação em
  # memória, com custo proporcional ao que o cliente realmente perdeu. É o que
  # sustenta "zero mensagens perdidas" ao derrubar um nó.
  # ---------------------------------------------------------------------
  hash_key  = "room_id"
  range_key = "seq"

  attribute {
    name = "room_id"
    type = "S"
  }

  attribute {
    name = "seq"
    type = "N"
  }

  # Os demais atributos (sender, content, lamport, vector_clock, node_id, ts…)
  # NÃO são declarados: DynamoDB tem schema apenas para as chaves. Declarar um
  # atributo aqui sem usá-lo em índice é, inclusive, erro de validação.

  # On-demand: a carga de uma demonstração é irregular por natureza (zero na
  # maior parte do tempo, pico durante o teste de carga). Provisionar
  # capacidade exigiria estimar esse pico e pagar por ele ocioso.
  billing_mode = "PAY_PER_REQUEST"

  # TTL de 7 dias (o app grava o atributo `ttl` calculado a partir de
  # SALAVIVA_MESSAGE_TTL_DAYS). Expiração automática é gratuita no DynamoDB e
  # impede que a tabela cresça indefinidamente depois da entrega do trabalho.
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    # PITR é armazenamento contínuo cobrado, para proteger dado que aqui expira
    # em 7 dias e é de demonstração. Em produção, ligado.
    enabled = false
  }

  # Precisa ser false para o `terraform destroy` pós-apresentação não exigir
  # intervenção manual.
  deletion_protection_enabled = false

  tags = {
    Name = var.messages_table_name
  }
}

resource "aws_dynamodb_table" "rooms" {
  name         = var.rooms_table_name
  billing_mode = "PAY_PER_REQUEST"

  # Só partition key: a sala é lida sempre por identificador exato, nunca
  # varrida por faixa. Sort key aqui não teria consulta que a usasse.
  hash_key = "room_id"

  attribute {
    name = "room_id"
    type = "S"
  }

  deletion_protection_enabled = false

  tags = {
    Name = var.rooms_table_name
  }
}
