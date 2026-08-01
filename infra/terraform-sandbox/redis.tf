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
  # IP privado fixo, escolhido dentro da primeira subrede.
  #
  # Sem isso, o endereço do Redis entraria no `user_data` dos nós a partir de um
  # atributo computado — e qualquer recriação da instância mudaria o IP,
  # invalidando o Launch Template e forçando a substituição de todo o Auto
  # Scaling Group. Com IP fixo, o Redis pode ser recriado sem que os nós saibam.
  redis_ip = cidrhost(aws_subnet.publica[0].cidr_block, 10)
}

resource "aws_instance" "redis" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.tipo_instancia
  subnet_id              = aws_subnet.publica[0].id
  private_ip             = local.redis_ip
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
  EOT

  tags = {
    Name = "${var.nome}-redis"
    Role = "redis"
  }
}
