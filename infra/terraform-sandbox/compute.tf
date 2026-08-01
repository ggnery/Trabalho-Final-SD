# =============================================================================
# Launch Template + Auto Scaling Group
#
# É este arquivo que torna a demonstração do critério EC3 possível: derruba-se
# uma instância ao vivo e o ASG cria outra. Sem grupo de escala, "tolerância a
# falhas" seria uma afirmação sobre a AWS, não uma propriedade observável do
# sistema.
# =============================================================================

locals {
  # Persistência só funciona com instance profile: sem uma role anexada, a
  # instância não tem como autenticar no DynamoDB. Em vez de deixar o sistema
  # falhar em runtime com erro de credencial a cada mensagem, desligamos a
  # persistência aqui e o app usa o repositório nulo — o chat segue funcionando
  # em tempo real e só o replay de histórico fica indisponível.
  persistencia = var.persistencia_habilitada && var.instance_profile != ""

  constroi_na_instancia = var.imagem_docker == ""

  # Construir a imagem na instância leva alguns minutos; baixar uma pronta leva
  # segundos. O período de carência do health check precisa cobrir o pior caso,
  # senão o ASG mata a instância no meio do build e entra em ciclo infinito de
  # substituição — um modo de falha que parece "a aplicação não sobe".
  carencia_health_check = local.constroi_na_instancia ? 600 : 240

  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    exec > >(tee /var/log/salaviva-boot.log) 2>&1

    dnf install -y docker git
    systemctl enable --now docker

    # 2 GiB de swap. A t3.micro tem 1 GiB de RAM e o build da imagem Python
    # estoura esse limite com facilidade; sem swap, o build morre com OOM e a
    # instância nunca fica saudável.
    if [ ! -f /swapfile ]; then
      dd if=/dev/zero of=/swapfile bs=1M count=2048
      chmod 600 /swapfile
      mkswap /swapfile
      swapon /swapfile
    fi

    # NODE_ID = ID da instância, obtido via IMDSv2.
    #
    # É o detalhe que faz a demonstração de falha funcionar: o node_id que
    # aparece no /dashboard e no cabeçalho de cada cliente é literalmente o
    # i-0abc... que se vê no console da EC2. Quando o professor pedir para
    # derrubar um nó, dá para apontar exatamente qual sumiu.
    TOKEN=$(curl -sX PUT "http://169.254.169.254/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
    NODE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
      http://169.254.169.254/latest/meta-data/instance-id)

    %{if local.constroi_na_instancia~}
    # Sem ECR na sandbox: a instância clona o repositório público e constrói a
    # imagem localmente. Custa minutos de boot, mas não exige registro de
    # imagens nem credencial alguma.
    git clone --depth 1 --branch ${var.branch_git} ${var.repositorio_git} /opt/salaviva
    cd /opt/salaviva
    docker build -t salaviva:local .
    IMAGEM=salaviva:local
    %{else~}
    docker pull ${var.imagem_docker}
    IMAGEM=${var.imagem_docker}
    %{endif~}

    docker run -d \
      --name salaviva \
      --restart always \
      -p 8000:8000 \
      -e SALAVIVA_NODE_ID="$NODE_ID" \
      -e SALAVIVA_REDIS_URL="redis://${local.redis_ip}:6379/0" \
      -e SALAVIVA_AWS_REGION="${var.aws_region}" \
      -e SALAVIVA_MESSAGES_TABLE="${aws_dynamodb_table.mensagens.name}" \
      -e SALAVIVA_ROOMS_TABLE="${aws_dynamodb_table.salas.name}" \
      -e SALAVIVA_PERSISTENCE_ENABLED="${local.persistencia ? "true" : "false"}" \
      -e SALAVIVA_JWT_SECRET="${var.jwt_secret}" \
      -e SALAVIVA_ENVIRONMENT="academy-sandbox" \
      -e SALAVIVA_PORT="8000" \
      -e SALAVIVA_LOG_JSON="true" \
      "$IMAGEM"
  EOT
}

resource "aws_launch_template" "app" {
  name_prefix   = "${var.nome}-lt-"
  image_id      = data.aws_ssm_parameter.al2023.value
  instance_type = var.tipo_instancia

  vpc_security_group_ids = [aws_security_group.app.id]

  # A profile NÃO é criada aqui — IAM é read-only na sandbox. Anexamos uma que
  # já existe (LabInstanceProfile). Se o nome vier vazio, nenhuma é anexada.
  dynamic "iam_instance_profile" {
    for_each = var.instance_profile == "" ? [] : [1]
    content {
      name = var.instance_profile
    }
  }

  metadata_options {
    http_tokens = "required" # IMDSv2 obrigatório
    # hop_limit 2: o processo roda dentro de um container, e um salto a mais é
    # necessário para que ele alcance o serviço de metadados do host. Com o
    # padrão 1, o boto3 dentro do container não obtém credencial e toda escrita
    # no DynamoDB falha.
    http_put_response_hop_limit = 2
    http_endpoint               = "enabled"
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 20 # a sandbox permite até 35 GB em gp2
      volume_type = "gp3"
      encrypted   = true
    }
  }

  user_data = base64encode(local.user_data)

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.nome}-no"
      Role = "app"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "app" {
  name_prefix         = "${var.nome}-asg-"
  vpc_zone_identifier = aws_subnet.publica[*].id

  min_size         = var.nos_min
  desired_capacity = var.nos_desejados
  max_size         = var.nos_max

  launch_template {
    id = aws_launch_template.app.id
    # "$Latest" e não um número fixo: ao mudar o user_data, o Terraform cria uma
    # nova versão do template e o instance_refresh substitui os nós usando-a.
    version = "$Latest"
  }

  target_group_arns = [aws_lb_target_group.app.arn]

  # "ELB", não "EC2". Com health check de EC2, o ASG só percebe a instância
  # morta quando o hardware falha — um processo travado passaria despercebido.
  # Com "ELB", o veredito é o do /readyz, que verifica Redis e DynamoDB.
  health_check_type         = "ELB"
  health_check_grace_period = local.carencia_health_check

  # Substituição rolante ao mudar o Launch Template (ex.: nova versão do
  # código), mantendo metade da capacidade em serviço durante a troca.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
    }
  }

  tag {
    key                 = "Name"
    value               = "${var.nome}-no"
    propagate_at_launch = true
  }

  tag {
    key                 = "Project"
    value               = "SalaViva"
    propagate_at_launch = true
  }

  # O Redis precisa estar de pé antes dos nós: um nó que sobe sem Redis reprova
  # no /readyz e é substituído pelo ASG, em ciclo.
  depends_on = [aws_instance.redis]

  lifecycle {
    create_before_destroy = true
    # Permite ajustar a capacidade pelo console durante a demonstração sem que
    # o próximo `terraform apply` a reverta.
    ignore_changes = [desired_capacity]
  }
}
