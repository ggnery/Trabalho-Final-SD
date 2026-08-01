# ---------------------------------------------------------------------------
# alb.tf — Application Load Balancer, Target Group e Listeners
#
# POR QUE ALB E NÃO NLB/CLB: o ALB é o balanceador da AWS que entende o
# handshake HTTP/1.1 `Upgrade: websocket` e mantém a conexão aberta depois do
# upgrade, além de fazer health check ativo em nível de aplicação (HTTP GET com
# verificação de status). O NLB balancearia TCP sem enxergar /readyz; o CLB é
# legado. Como o mesmo processo serve HTTP (UI, /metrics, health) e WebSocket,
# um único target group cobre tudo.
# ---------------------------------------------------------------------------

resource "aws_lb" "main" {
  name               = "${local.name_prefix}-alb"
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]

  # Uma subrede por AZ. O ALB exige ≥ 2 AZs e passa a resolver para um IP em
  # cada uma: se uma AZ cair, o DNS continua entregando a outra.
  subnets = [for s in aws_subnet.public : s.id]

  # DECISÃO CRÍTICA PARA WEBSOCKET: o padrão do ALB é derrubar conexões ociosas
  # em 60 s. O heartbeat da aplicação é de 20 s, então tecnicamente 60 s
  # bastaria — mas qualquer atraso de dois pings mataria a conexão e o cliente
  # veria uma reconexão "espontânea" no meio da apresentação. 300 s dá margem
  # ampla e mantém a conexão de longa duração de fato longa.
  idle_timeout = 300

  # Precisa ficar `false` para que `terraform destroy` funcione sem intervenção
  # manual. Em produção seria `true`.
  enable_deletion_protection = false

  # Sem `drop_invalid_header_fields` (padrão false): o upgrade WebSocket carrega
  # cabeçalhos (Sec-WebSocket-*) que não vale a pena arriscar filtrar.

  tags = {
    Name = "${local.name_prefix}-alb"
  }
}

resource "aws_lb_target_group" "app" {
  name        = "${local.name_prefix}-tg"
  port        = var.app_port
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = aws_vpc.main.id

  # ---------------------------------------------------------------------
  # DESREGISTRO RÁPIDO — o parâmetro mais importante para a demo do EC3.
  #
  # O padrão da AWS é 300 s: ao remover um alvo, o ALB espera 5 minutos
  # drenando conexões antes de esquecê-lo. Numa apresentação de 15 minutos,
  # isso significaria "derrubei a instância e nada visível aconteceu".
  # Com 10 s, o alvo sai do pool quase imediatamente e o efeito da falha
  # (clientes reconectando em outro nó, nó sumindo do /dashboard) acontece
  # dentro da janela de atenção da banca.
  #
  # Trade-off assumido: conexões WebSocket em curso naquele alvo são cortadas
  # em vez de drenadas. É aceitável — e na verdade desejável — porque o que
  # queremos demonstrar é justamente a reconexão sem perda de mensagens.
  # ---------------------------------------------------------------------
  deregistration_delay = 10

  health_check {
    enabled = true

    # ---------------------------------------------------------------------
    # /readyz, NÃO /healthz.
    #
    # /healthz responde 200 enquanto o PROCESSO estiver vivo. Um nó que perdeu
    # a conexão com o Redis continuaria "saudável" ali e seguiria recebendo
    # conexões WebSocket que ele não consegue servir — um buraco negro: o
    # cliente conecta, entra na sala e nunca recebe nada, sem erro visível.
    #
    # /readyz verifica as dependências (bus Redis + repositório DynamoDB) e
    # devolve 503 quando alguma falha. O nó degradado se autoexclui do pool.
    # É esta escolha que faz o failover ser automático em vez de manual.
    # ---------------------------------------------------------------------
    path     = "/readyz"
    protocol = "HTTP"
    port     = "traffic-port"
    matcher  = "200"

    # 15 s × 3 falhas = nó doente detectado em ≤ 45 s (NFR de Confiabilidade).
    # Intervalos menores custariam mais requisições sem ganho perceptível.
    interval = 15

    # Precisa ser menor que `interval`. 5 s é folgado para um endpoint que faz
    # dois PINGs internos.
    timeout = 5

    # Assimetria deliberada: 2 para entrar (nó novo do ASG começa a receber
    # tráfego rápido, restaurando capacidade) e 3 para sair (um 503 isolado, por
    # um pico momentâneo do Redis, não deve tirar um nó bom do ar).
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # ---------------------------------------------------------------------
  # STICKINESS (ADR-007).
  #
  # O sistema NÃO depende de afinidade para funcionar (ADR-007): a conexão
  # WebSocket não migra depois de estabelecida, o JWT é stateless e, ao
  # reconectar, o cliente refaz `join` com o seu last_seq e recupera o backlog
  # em QUALQUER nó — porque o número de sequência vive no Redis e o histórico
  # no DynamoDB. Mantemos stickiness apenas como estabilizador do handshake
  # HTTP que precede o upgrade.
  #
  # Custo: distribuição levemente desigual de conexões entre os nós, visível no
  # /dashboard. Por esse motivo o balanceador local (nginx) usa `least_conn` em
  # vez de afinidade: medimos que o ip_hash concentrava 100% das conexões de um
  # mesmo host em um único nó, o que apagaria a evidência de distribuição na
  # demonstração.
  # ---------------------------------------------------------------------
  stickiness {
    type            = "lb_cookie"
    enabled         = true
    cookie_duration = 3600
  }

  tags = {
    Name = "${local.name_prefix}-tg"
  }

  # Nome fixo, sem `create_before_destroy`: o nome de um target group tem teto de
  # 32 caracteres e o `name_prefix` correspondente aceita no máximo 6, o que
  # produziria um nome ilegível no console. Nada aqui força recriação no uso
  # normal (porta, protocolo e VPC são estáveis) — o que muda são atributos
  # aplicados no lugar, como o health check.
}

# --- Listener HTTP (80) ----------------------------------------------------
# Encaminha sempre, inclusive quando existe HTTPS. Não fazemos redirect 80→443
# porque, sem domínio próprio, o acesso da demo é pelo DNS do ALB em HTTP —
# um redirect obrigatório quebraria a apresentação.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

# --- Listener HTTPS (443) — condicional ------------------------------------
# Só existe se `certificate_arn` for informado. O ACM exige um domínio validável
# e o DNS do ALB não é validável, então a demo roda em HTTP/WS. Deixar o caminho
# pronto (em vez de omitir) mostra que a lacuna é de contexto, não de desenho.
resource "aws_lb_listener" "https" {
  count = var.certificate_arn == "" ? 0 : 1

  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.certificate_arn

  # TLS 1.2 como piso (requisito de segurança em Requirements/NFR).
  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
