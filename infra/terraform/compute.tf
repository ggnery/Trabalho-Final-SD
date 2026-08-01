# ---------------------------------------------------------------------------
# compute.tf — Launch Template, Auto Scaling Group e logs dos nós
#
# É aqui que mora a demonstração do critério EC3. O ASG não está no projeto para
# escalar por carga (a demo não gera carga suficiente para isso): está para que
# exista uma INSTÂNCIA DERRUBÁVEL e um mecanismo que a reponha sozinho, ao vivo,
# enquanto o chat continua funcionando nos nós restantes.
# ---------------------------------------------------------------------------

# --- AMI -------------------------------------------------------------------
# Amazon Linux 2023 resolvido por SSM Public Parameter em vez de um ID fixo.
# Um `ami-0123…` no código apodrece: fica sem patch e some quando a AWS
# despublica a imagem, quebrando o `apply` do avaliador meses depois.
#
# Efeito colateral a saber: quando a AWS publica uma AMI nova, o Launch Template
# muda e o `instance_refresh` abaixo recicla as instâncias no próximo `apply`.
# É o comportamento desejado (nós sempre com o SO corrigido), mas não deve
# surpreender ninguém no dia da apresentação — por isso está escrito aqui.
data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# --- Logs ------------------------------------------------------------------
# O log do container vai para o CloudWatch pelo driver `awslogs`. O motivo é
# direto e ligado à demo: o log de uma instância derrubada morre com ela, e a
# evidência da falha (o nó parando de publicar, os clientes reconectando) é
# justamente o que precisamos mostrar DEPOIS do evento. Log centralizado
# sobrevive à instância.
resource "aws_cloudwatch_log_group" "app" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.name_prefix}-logs"
  }
}

locals {
  # Host do registro ECR (…dkr.ecr.us-east-1.amazonaws.com), extraído da URL do
  # repositório. É o argumento do `docker login`.
  ecr_registry = split("/", aws_ecr_repository.app.repository_url)[0]

  container_image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

  # -------------------------------------------------------------------------
  # user_data — bootstrap do nó.
  #
  # Ordem: docker → identidade (IMDSv2) → login/pull no ECR → segredo do SSM →
  # arquivo de ambiente 0600 → container.
  #
  # Nenhum segredo aparece aqui dentro: o user_data é legível por qualquer
  # processo da instância (e pelo console da AWS). O que trafega é o NOME do
  # parâmetro; o valor é buscado em runtime com as credenciais do instance
  # profile. Por isso também não usamos `set -x`: ele imprimiria o segredo em
  # /var/log/cloud-init-output.log.
  # -------------------------------------------------------------------------
  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail

    # --- 1. Docker ---------------------------------------------------------
    # Laço de tentativa porque a instância acabou de subir e o espelho de
    # pacotes pode ainda não estar alcançável — uma falha aqui deixaria o nó
    # vivo mas inútil, reprovando no /readyz e sendo substituído em loop.
    for _ in 1 2 3; do
      dnf install -y docker && break
      sleep 10
    done
    systemctl enable --now docker

    # A AMI do Amazon Linux 2023 já traz a AWS CLI v2, usada nos passos 3 e 4.
    # A guarda existe só para não depender disso silenciosamente: se um dia a
    # imagem base mudar, a falha aqui é explícita em vez de um "aws: command
    # not found" no meio do bootstrap.
    if ! command -v aws >/dev/null 2>&1; then
      dnf install -y awscli-2
    fi

    # --- 2. Identidade do nó via IMDSv2 -----------------------------------
    # O node_id é o ID da instância EC2 (i-0abc…), e isso é deliberado: na
    # demonstração de falha, o identificador que some do /dashboard é
    # LITERALMENTE o mesmo que o professor vê no console ao terminar a
    # instância. Sem isso, o painel mostraria um hostname interno e a
    # correspondência entre "o que eu derrubei" e "o que sumiu" ficaria por
    # conta da confiança.
    #
    # IMDSv2 (token via PUT) porque o Launch Template exige `http_tokens =
    # "required"` — IMDSv1 é vulnerável a SSRF.
    IMDS_TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
    INSTANCE_ID=$(curl -sS -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
      "http://169.254.169.254/latest/meta-data/instance-id")

    # --- 3. Imagem do ECR --------------------------------------------------
    # Sem credencial nenhuma no disco: o `aws ecr get-login-password` usa o
    # instance profile e devolve um token de 12 h.
    aws ecr get-login-password --region ${var.aws_region} \
      | docker login --username AWS --password-stdin ${local.ecr_registry}
    docker pull ${local.container_image}

    # --- 4. Segredo JWT (SSM Parameter Store, SecureString) ---------------
    JWT_SECRET=$(aws ssm get-parameter \
      --name "${local.jwt_parameter_name}" \
      --with-decryption \
      --region ${var.aws_region} \
      --query Parameter.Value \
      --output text)

    # --- 5. Ambiente do container -----------------------------------------
    # Arquivo com umask 077 em vez de vários `-e` na linha de comando: com
    # `-e`, o segredo apareceria em `ps aux` e em `docker inspect`.
    #
    # SALAVIVA_DYNAMO_ENDPOINT_URL fica ausente de propósito — vazio significa
    # "DynamoDB real"; ele só é preenchido no Compose local, que aponta para o
    # DynamoDB Local.
    umask 077
    cat > /etc/salaviva.env <<EOF
    SALAVIVA_NODE_ID=$INSTANCE_ID
    SALAVIVA_ENVIRONMENT=${var.environment}
    SALAVIVA_PORT=${var.app_port}
    SALAVIVA_REDIS_URL=redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0
    SALAVIVA_AWS_REGION=${var.aws_region}
    SALAVIVA_MESSAGES_TABLE=${aws_dynamodb_table.messages.name}
    SALAVIVA_ROOMS_TABLE=${aws_dynamodb_table.rooms.name}
    SALAVIVA_PERSISTENCE_ENABLED=${var.persistence_enabled}
    SALAVIVA_JWT_SECRET=$JWT_SECRET
    SALAVIVA_LOG_JSON=true
    SALAVIVA_LOG_LEVEL=${var.log_level}
    EOF

    # --- 6. Container ------------------------------------------------------
    # --restart unless-stopped cobre a falha do processo e o reboot da
    # instância. A falha da instância inteira é coberta pelo ASG.
    # O stream de log recebe o nome da instância: no CloudWatch, um stream por
    # nó, o que permite ler o que o nó derrubado fez até o último instante.
    docker rm -f salaviva >/dev/null 2>&1 || true
    docker run -d \
      --name salaviva \
      --restart unless-stopped \
      --env-file /etc/salaviva.env \
      -p ${var.app_port}:${var.app_port} \
      --log-driver=awslogs \
      --log-opt awslogs-region=${var.aws_region} \
      --log-opt awslogs-group=${local.log_group_name} \
      --log-opt awslogs-stream="$INSTANCE_ID" \
      ${local.container_image}

    # --- 7. Espera ativa, só para diagnóstico ------------------------------
    # Não muda o resultado (quem decide se o nó entra no pool é o health check
    # do ALB em /readyz). Serve para que, ao abrir
    # /var/log/cloud-init-output.log em uma instância problemática, a última
    # linha diga se a aplicação chegou a responder.
    for _ in $(seq 1 30); do
      if curl -sf "http://localhost:${var.app_port}/healthz" >/dev/null; then
        echo "salaviva: no $INSTANCE_ID respondendo em /healthz"
        exit 0
      fi
      sleep 2
    done
    echo "salaviva: aplicacao nao respondeu em 60s; ver 'docker logs salaviva'" >&2
  EOT
}

# --- Launch Template -------------------------------------------------------

resource "aws_launch_template" "app" {
  name_prefix   = "${local.name_prefix}-lt-"
  image_id      = data.aws_ssm_parameter.al2023_ami.value
  instance_type = var.instance_type

  # Só define a chave se ela foi informada; lista vazia = nenhuma chave, que é
  # o padrão desejado (acesso administrativo é exceção, ver security.tf).
  key_name = var.key_pair_name == "" ? null : var.key_pair_name

  iam_instance_profile {
    name = aws_iam_instance_profile.app.name
  }

  vpc_security_group_ids = [aws_security_group.app.id]

  # user_data precisa ir em base64 para a API da EC2.
  user_data = base64encode(local.user_data)

  metadata_options {
    http_endpoint = "enabled"

    # IMDSv2 obrigatório: bloqueia o acesso ao metadata por requisição GET
    # simples, que é o vetor de roubo de credenciais do instance profile via
    # SSRF na aplicação.
    http_tokens = "required"

    # ---------------------------------------------------------------------
    # HOP LIMIT 2 — não é firula, é requisito funcional aqui.
    #
    # O padrão é 1, que só permite acesso ao metadata a partir do host. Nossa
    # aplicação roda DENTRO de um container em rede bridge: o pacote atravessa
    # a bridge do Docker e isso consome um hop. Com o limite em 1, o aioboto3
    # não conseguiria obter credenciais e TODA escrita no DynamoDB falharia —
    # com um erro de credencial, difícil de associar à causa.
    # ---------------------------------------------------------------------
    http_put_response_hop_limit = 2
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size = 8
      volume_type = "gp3"
      encrypted   = true
      # Some junto com a instância — sem isso, cada nó derrubado na demo
      # deixaria um volume órfão sendo cobrado.
      delete_on_termination = true
    }
  }

  monitoring {
    # Métricas detalhadas (1 min) custam por instância. A granularidade de 5 min
    # do monitoramento básico é suficiente: quem observa a demo é o /dashboard
    # da própria aplicação, atualizado em segundos.
    enabled = false
  }

  # `default_tags` do provider não se propagam para as instâncias criadas por um
  # ASG. Repetimos as tags aqui para que instância e volume nasçam
  # identificáveis no console — inclusive a instância que o professor vai
  # escolher para derrubar.
  tag_specifications {
    resource_type = "instance"

    tags = {
      Name      = "${local.name_prefix}-node"
      Project   = "SalaViva"
      ManagedBy = "Terraform"
    }
  }

  tag_specifications {
    resource_type = "volume"

    tags = {
      Name      = "${local.name_prefix}-node-root"
      Project   = "SalaViva"
      ManagedBy = "Terraform"
    }
  }

  tags = {
    Name = "${local.name_prefix}-lt"
  }

  lifecycle {
    create_before_destroy = true
  }

  # A instância só é útil se as dependências já existirem quando o user_data
  # rodar. As referências acima (Redis, DynamoDB, ECR) já criam a ordem; o SSM
  # entra explicitamente porque só aparece dentro de uma string.
  depends_on = [
    aws_ssm_parameter.jwt_secret,
    aws_cloudwatch_log_group.app,
  ]
}

# --- Auto Scaling Group ----------------------------------------------------

resource "aws_autoscaling_group" "app" {
  name = "${local.name_prefix}-asg"

  min_size         = var.asg_min_size
  desired_capacity = var.asg_desired_capacity
  max_size         = var.asg_max_size

  # Distribui os nós pelas duas AZs públicas. Com 3 nós em 2 AZs, perder uma AZ
  # inteira deixa pelo menos um nó de pé — e o ASG repõe o resto.
  vpc_zone_identifier = [for s in aws_subnet.public : s.id]
  target_group_arns   = [aws_lb_target_group.app.arn]

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"
  }

  # ---------------------------------------------------------------------
  # health_check_type = "ELB", não "EC2".
  #
  # Com "EC2", o ASG só considera doente a instância cujo hardware/SO parou —
  # um container travado ou sem Redis contaria como saudável para sempre.
  # Com "ELB", o veredito do health check em /readyz também substitui a
  # instância. É a diferença entre "a máquina está ligada" e "o nó está
  # servindo o chat".
  # ---------------------------------------------------------------------
  health_check_type = "ELB"

  # Tempo de boot + dnf install + docker pull. Curto demais e o ASG mataria a
  # instância antes de a aplicação subir, entrando em laço de substituição.
  health_check_grace_period = 300

  # Teto para o `apply` esperar as instâncias nascerem. Deliberadamente NÃO
  # usamos `min_elb_capacity`: isso faria o `apply` esperar os alvos ficarem
  # saudáveis no ALB, e o primeiro `apply` (antes de existir imagem no ECR)
  # travaria por 10 minutos até falhar, em vez de terminar e deixar o operador
  # publicar a imagem.
  wait_for_capacity_timeout = "10m"

  # Ao reduzir a capacidade, remove a instância mais antiga. Torna o scale-in
  # previsível durante a apresentação, em vez de "alguma instância".
  termination_policies = ["OldestInstance"]

  # ---------------------------------------------------------------------
  # instance_refresh: substituição contínua quando o Launch Template muda
  # (nova imagem no ECR + nova tag, AMI nova, mudança de user_data).
  #
  # min_healthy_percentage = 50 mantém metade dos nós servindo durante a troca:
  # o chat não cai para publicar uma versão nova. É o mesmo mecanismo que
  # sustenta o argumento de escalabilidade/disponibilidade do EC2.
  # ---------------------------------------------------------------------
  instance_refresh {
    strategy = "Rolling"

    preferences {
      min_healthy_percentage = 50
      # Espera o nó novo aquecer (pull + boot) antes de contá-lo como saudável
      # e prosseguir com o próximo.
      instance_warmup = 300
    }

    triggers = ["tag"]
  }

  tag {
    key                 = "Name"
    value               = "${local.name_prefix}-node"
    propagate_at_launch = true
  }

  tag {
    key                 = "Project"
    value               = "SalaViva"
    propagate_at_launch = true
  }

  tag {
    key                 = "ManagedBy"
    value               = "Terraform"
    propagate_at_launch = true
  }

  lifecycle {
    # Sem `create_before_destroy` aqui, ao contrário dos outros recursos: o nome
    # do ASG é fixo e previsível (os scripts de demo e o console dependem dele),
    # e criar um substituto antes de destruir o original colidiria no nome.

    # A capacidade pode ser alterada à mão durante a apresentação (por exemplo,
    # para mostrar escala horizontal ao vivo). Sem isto, o próximo `apply`
    # desfaria a mudança silenciosamente.
    ignore_changes = [desired_capacity]
  }
}
