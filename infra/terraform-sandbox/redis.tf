# =============================================================================
# Redis em EC2 — substituto do ElastiCache
#
# O ElastiCache não está entre os serviços liberados na sandbox. Como o sistema
# usa apenas três recursos do Redis — PUB/SUB, INCR e ZSET —, todos presentes no
# Redis de código aberto, hospedá-lo num EC2 preserva o comportamento
# integralmente. O que se perde em relação ao serviço gerenciado é operacional,
# não funcional: backup automático, failover Multi-AZ e patching.
#
# É um ponto único de falha. Também era na infraestrutura de referência, que usa
# ElastiCache single-node por restrição de custo (ver ADR-002). A diferença é
# que ali a mitigação de produção é uma flag; aqui exigiria montar replicação
# manualmente. Declarado no README e no slide de limitações.
# =============================================================================

locals {
  # Endereço real da instância, e não um IP fixo escolhido a dedo.
  #
  # A primeira versão fixava o IP (`cidrhost(...)`) para que recriar o Redis não
  # invalidasse o Launch Template. Parecia mais desacoplado — e falhou na
  # prática: o `create_before_destroy` do Auto Scaling **se propaga** para tudo
  # de que ele depende, incluindo esta instância. O Terraform passa então a
  # tentar criar o Redis novo antes de destruir o antigo, e os dois disputam o
  # mesmo endereço:
  #
  #     Error: InvalidIPAddress.InUse: Address 10.30.0.10 is in use
  #
  # Usar o atributo computado resolve e ainda torna a dependência **implícita**:
  # o Terraform passa a saber, sem `depends_on`, que o Launch Template depende
  # do Redis existir. O custo é que recriar o Redis muda o template e dispara a
  # substituição dos nós — o que é o comportamento correto, já que eles precisam
  # mesmo aprender o novo endereço.
  redis_ip = aws_instance.redis.private_ip
}

resource "aws_instance" "redis" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.tipo_instancia
  subnet_id              = aws_subnet.publica[0].id
  vpc_security_group_ids = [aws_security_group.redis.id]

  metadata_options {
    http_tokens   = "required" # IMDSv2 obrigatório
    http_endpoint = "enabled"
  }

  user_data_replace_on_change = true
  user_data                   = <<-EOT
    #!/bin/bash
    set -euxo pipefail

    dnf install -y docker
    systemctl enable --now docker

    # --appendonly yes: o contador de sequência (INCR chat:seq:{sala}) é o que
    # define a ordem total. Sem persistência, um restart do Redis zeraria o
    # contador e dois eventos distintos receberiam o mesmo número — a ordem
    # total quebraria silenciosamente, que é o pior modo de falha possível
    # neste sistema.
    docker run -d \
      --name redis \
      --restart always \
      -p 6379:6379 \
      redis:7-alpine \
      redis-server --appendonly yes --save ""

    %{if var.dynamodb_local~}
    # DynamoDB Local no mesmo host, porque a sandbox nega iam:PassRole e as
    # instâncias do Auto Scaling não podem receber credencial para falar com o
    # DynamoDB gerenciado (ver variables.tf > dynamodb_local).
    #
    # -sharedDb: sem esta flag, cada par de credenciais enxerga um banco
    # separado. Como os três nós usam credenciais fictícias idênticas isso já
    # funcionaria, mas a flag torna o comportamento explícito em vez de
    # acidental.
    docker run -d \
      --name dynamodb \
      --restart always \
      -p 8009:8000 \
      amazon/dynamodb-local:latest \
      -jar DynamoDBLocal.jar -sharedDb -inMemory
    %{endif~}
  EOT

  tags = {
    Name = "${var.nome}-redis"
    Role = "redis"
  }
}
