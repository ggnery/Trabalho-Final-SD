# =============================================================================
# Application Load Balancer
#
# O ALB é o único tipo de balanceador da AWS que faz o upgrade de HTTP/1.1 para
# WebSocket. Sem ele — com um Network Load Balancer, por exemplo — o handshake
# em /ws devolveria 400 e não haveria chat.
# =============================================================================

resource "aws_lb" "app" {
  name               = "${var.nome}-alb"
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.publica[*].id

  # Uma conexão de chat fica minutos ociosa entre mensagens. Com o padrão de
  # 60 s o ALB a derrubaria, e o cliente entraria num ciclo de reconexão que
  # pareceria "instabilidade do sistema". 300 s fica folgadamente acima do
  # heartbeat de 20 s do servidor.
  idle_timeout = 300

  tags = { Name = "${var.nome}-alb" }
}

resource "aws_lb_target_group" "app" {
  name        = "${var.nome}-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.principal.id
  target_type = "instance"

  health_check {
    enabled = true

    # /readyz, e não /healthz.
    #
    # /healthz responde 200 enquanto o PROCESSO estiver vivo. Um nó que perdeu
    # a conexão com o Redis continuaria "saudável" ali e seguiria recebendo
    # conexões que não consegue servir — um buraco negro. /readyz verifica as
    # dependências, então o nó degradado se autoexclui do pool.
    #
    # É essa distinção que torna o failover automático em vez de manual.
    path                = "/readyz"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # 10 s, e não os 300 s padrão. Na demonstração de falha, é o tempo que o ALB
  # leva para parar de mandar tráfego a um alvo removido. Com o padrão, a
  # plateia esperaria cinco minutos olhando para uma tela parada.
  deregistration_delay = 10

  stickiness {
    type            = "lb_cookie"
    enabled         = true
    cookie_duration = 3600
  }

  tags = { Name = "${var.nome}-tg" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
