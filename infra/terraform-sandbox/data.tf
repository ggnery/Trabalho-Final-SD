# =============================================================================
# DynamoDB — histórico durável
#
# Único componente que atravessa sem alteração da infraestrutura de referência
# para a sandbox: o DynamoDB está liberado.
#
# A modelagem é a mesma e a razão dela também: `room_id` como partition key e
# `seq` como **sort key** fazem o replay de reconexão ser uma única Query com
# `seq > :last`, já devolvida em ordem crescente pelo índice. É o que sustenta a
# demonstração de "zero mensagens perdidas" ao derrubar um nó.
# =============================================================================

resource "aws_dynamodb_table" "mensagens" {
  name         = "${var.nome}_messages"
  billing_mode = "PAY_PER_REQUEST" # sem capacidade provisionada para estimar

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

  # Expira mensagens após 7 dias (o app grava o atributo `ttl`). Contém volume e
  # custo sem exigir rotina de limpeza.
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = { Name = "${var.nome}-messages" }
}

resource "aws_dynamodb_table" "salas" {
  name         = "${var.nome}_rooms"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "room_id"

  attribute {
    name = "room_id"
    type = "S"
  }

  tags = { Name = "${var.nome}-rooms" }
}
