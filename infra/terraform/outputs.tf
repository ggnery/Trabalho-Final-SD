# ---------------------------------------------------------------------------
# outputs.tf — o que o operador precisa depois do `apply`
#
# Cada saída existe para um uso concreto: abrir a demo, publicar a imagem,
# derrubar um nó ou investigar o que aconteceu. Nada aqui é decorativo.
# ---------------------------------------------------------------------------

output "alb_dns_name" {
  description = "DNS publico do Application Load Balancer. Ponto de entrada unico do sistema."
  value       = aws_lb.main.dns_name
}

output "chat_url" {
  description = "URL do cliente web. E o que se abre no navegador para a demonstracao."
  value       = "http://${aws_lb.main.dns_name}/"
}

output "dashboard_url" {
  description = <<-EOT
    Painel de nos vivos. E A TELA DA DEMONSTRACAO DE FALHA (EC3): ao terminar uma
    instancia, o node_id correspondente (o proprio i-xxxx da EC2) desaparece
    daqui em ate 15 s, e o no criado pelo Auto Scaling aparece com id novo.
  EOT
  value       = "http://${aws_lb.main.dns_name}/dashboard"
}

output "ecr_repository_url" {
  description = "Repositorio de imagens. Alvo do `docker push` (ver README, secao de deploy)."
  value       = aws_ecr_repository.app.repository_url
}

output "redis_endpoint" {
  description = <<-EOT
    Endpoint do ElastiCache, em subrede privada. Nao e alcancavel de fora da VPC
    — aparece aqui para diagnostico e para montar o SALAVIVA_REDIS_URL a mao
    quando se depura de dentro de uma instancia.
  EOT
  value       = "${aws_elasticache_cluster.redis.cache_nodes[0].address}:${aws_elasticache_cluster.redis.port}"
}

output "asg_name" {
  description = <<-EOT
    Nome do Auto Scaling Group. Consumido pelos scripts de demonstracao para
    listar as instancias e escolher uma para derrubar, por exemplo:

      aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names <asg_name> \
        --query 'AutoScalingGroups[0].Instances[*].InstanceId'
  EOT
  value       = aws_autoscaling_group.app.name
}

output "region" {
  description = "Regiao AWS onde tudo foi provisionado. Necessaria em todo comando da AWS CLI."
  value       = var.aws_region
}

# --- Auxiliares de operação ------------------------------------------------

output "target_group_arn" {
  description = <<-EOT
    Target Group do ALB. E a fonte da verdade sobre quais nos estao saudaveis:

      aws elbv2 describe-target-health --target-group-arn <target_group_arn>

    Durante a demo de falha, e aqui que se ve o alvo passar por
    healthy -> draining -> sumir, e o substituto entrar como healthy.
  EOT
  value       = aws_lb_target_group.app.arn
}

output "cloudwatch_log_group" {
  description = <<-EOT
    Grupo de logs dos containers, um stream por instancia. Sobrevive a morte do
    no — e onde se le o que a instancia derrubada fez ate o ultimo instante.
  EOT
  value       = aws_cloudwatch_log_group.app.name
}

output "vpc_id" {
  description = "VPC do ambiente. Util para inspecionar Security Groups e subredes no console."
  value       = aws_vpc.main.id
}

output "jwt_parameter_name" {
  description = <<-EOT
    Caminho do segredo JWT no SSM Parameter Store. O VALOR nao e exposto como
    output de proposito — sai apenas para a instancia autorizada, em runtime.
  EOT
  value       = aws_ssm_parameter.jwt_secret.name
}
