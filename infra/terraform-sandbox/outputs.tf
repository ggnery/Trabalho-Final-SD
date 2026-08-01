# =============================================================================
# Saídas — o que você precisa depois do apply
# =============================================================================

output "chat_url" {
  description = "Abra isto no navegador. É o endereço da demonstração."
  value       = "http://${aws_lb.app.dns_name}"
}

output "dashboard_url" {
  description = "Painel de nós vivos — projete durante a simulação de falha."
  value       = "http://${aws_lb.app.dns_name}/dashboard"
}

output "verificar_nos" {
  description = "Comando que confirma quantos nós estão no cluster."
  value       = "curl -s http://${aws_lb.app.dns_name}/api/nodes | python3 -m json.tool"
}

output "alb_dns_name" {
  description = "DNS do balanceador."
  value       = aws_lb.app.dns_name
}

output "asg_name" {
  description = "Nome do Auto Scaling Group (usado por scripts/kill_node.sh --aws)."
  value       = aws_autoscaling_group.app.name
}

output "redis_ip_privado" {
  description = "IP privado do Redis, para depuração a partir de um nó."
  value       = local.redis_ip
}

output "persistencia_ativa" {
  description = <<-EOT
    Se false, o histórico não está sendo gravado no DynamoDB — provavelmente
    porque `instance_profile` ficou vazia. O chat funciona em tempo real, mas o
    replay na reconexão não.
  EOT
  value       = local.persistencia
}

output "tempo_estimado_de_boot" {
  description = "Quanto esperar até os nós entrarem em serviço."
  value = local.constroi_na_instancia ? (
    "~4-6 min (a instancia clona o repositorio e constroi a imagem). Defina 'imagem_docker' para reduzir a ~1 min."
  ) : "~1-2 min (imagem pronta, apenas docker pull)."
}

output "LEMBRETE_CUSTO" {
  description = "Leia."
  value       = <<-EOT

    Orçamento da sandbox: US$ 20 no total, sem reposição.
    Este ambiente consome aproximadamente US$ 0,07 por hora enquanto estiver de pé.

    Ao terminar CADA sessão de estudo:
        terraform destroy        (destrói tudo)
        e depois "End Lab" no Vocareum

    Deixar de pé por uma semana consome mais da metade do seu crédito.
    Se o crédito acabar, a conta é bloqueada e TODOS os recursos são apagados.
  EOT
}
