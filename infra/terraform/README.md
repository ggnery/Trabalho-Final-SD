# Infraestrutura AWS do SalaViva (Terraform)

Provisionamento completo do ambiente de nuvem do **SalaViva** — chat distribuído
em tempo real com salas, Projeto Final de Sistemas Distribuídos.

Tudo aqui é código: `terraform apply` cria o ambiente do zero e
`terraform destroy` o remove por inteiro. Nenhum recurso é criado à mão no
console.

---

## O que este código cria

| Camada | Recurso | Papel |
|---|---|---|
| Rede | VPC, 2 subredes públicas, 2 subredes privadas, Internet Gateway, Gateway Endpoint do DynamoDB | Isolamento em 2 AZs. **Sem NAT Gateway** (ADR-006) |
| Segurança | 3 Security Groups **encadeados** | `alb` → `app` → `redis`, cada um aceitando só o anterior |
| Borda | Application Load Balancer + Target Group | WebSocket, health check em `/readyz`, sticky sessions |
| Aplicação | Launch Template + Auto Scaling Group (min 2, desired 3, max 4) | Os nós derrubáveis da demonstração de falha |
| Coordenação | ElastiCache for Redis `cache.t3.micro` | Pub/Sub, `INCR` (ordem total), ZSETs de presença e de nós |
| Histórico | DynamoDB `salaviva_messages` + `salaviva_rooms` | Replay por `last_seq`, TTL de 7 dias |
| Imagem | ECR com lifecycle policy | `docker pull` autenticado por IAM role |
| Identidade | IAM role, instance profile, policy de menor privilégio | Sem credencial de longa duração em lugar nenhum |
| Segredo | SSM Parameter Store `SecureString` | Segredo JWT lido no boot, nunca na imagem |
| Logs | CloudWatch Log Group | Sobrevive à instância derrubada |

Arquitetura completa e justificativas em
[`memory-bank/standards/system-architecture.md`](../../memory-bank/standards/system-architecture.md)
e [`memory-bank/standards/decision-index.md`](../../memory-bank/standards/decision-index.md).

---

## Pré-requisitos

| Ferramenta | Versão mínima | Verificar |
|---|---|---|
| Terraform | 1.5 | `terraform version` |
| AWS CLI | 2.x | `aws --version` |
| Docker | 20.10 (com `buildx`) | `docker version` |
| Conta AWS | Free Tier ativo | `aws sts get-caller-identity` |

**Credenciais AWS** configuradas com permissão para criar VPC, EC2, ELB,
ElastiCache, DynamoDB, ECR, IAM e SSM:

```bash
aws configure          # ou: export AWS_PROFILE=...
aws sts get-caller-identity   # deve devolver sua conta
```

**Segredo JWT** — gere o seu antes de começar:

```bash
openssl rand -hex 32
```

> **Nunca versione segredo nem estado.** O `.gitignore` da raiz do repositório
> ainda não cobre os artefatos do Terraform. Acrescente, antes do primeiro
> `apply`:
>
> ```gitignore
> infra/terraform/.terraform/
> infra/terraform/*.tfstate
> infra/terraform/*.tfstate.*
> infra/terraform/*.tfvars
> !infra/terraform/terraform.tfvars.example
> ```
>
> O `terraform.tfvars` contém o segredo JWT em texto claro, e o
> `terraform.tfstate` também (é uma característica do Terraform, não deste
> projeto).

---

## Deploy — passo a passo

O deploy tem **duas fases** por um motivo específico: as instâncias EC2 baixam a
imagem do ECR no boot, e o ECR só existe depois do Terraform. Criar tudo de uma
vez faria os nós subirem, não acharem imagem, reprovarem no health check e serem
substituídos em laço até alguém publicar a imagem.

### 1. Configurar

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars          # cole o jwt_secret gerado
```

### 2. Inicializar

```bash
terraform init
```

Baixa o provider AWS e prepara o backend local (`terraform.tfstate` nesta pasta).

### 3. Fase 1 — criar apenas o repositório de imagens

```bash
terraform apply -target=aws_ecr_repository.app
```

### 4. Construir e publicar a imagem

```bash
# a partir da RAIZ do repositório
cd ../..

REGION=$(cd infra/terraform && terraform output -raw region)
ECR=$(cd infra/terraform && terraform output -raw ecr_repository_url)

# login no ECR
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ECR%%/*}"

# build para linux/amd64 — OBRIGATÓRIO em Mac com chip Apple Silicon:
# a imagem nativa seria arm64 e a t3.micro é x86_64. Sem --platform, o
# container falha no boot com "exec format error", que aparece como um nó que
# nunca fica saudável.
docker buildx build --platform linux/amd64 -t "$ECR:latest" --push .
```

Se a máquina não tiver `buildx` e já for x86_64:

```bash
docker build -t "$ECR:latest" .
docker push "$ECR:latest"
```

### 5. Fase 2 — revisar e criar o resto

```bash
cd infra/terraform
terraform plan      # LEIA a saída: confira que nada inesperado será criado
terraform apply
```

Leva de 8 a 12 minutos — o ElastiCache é o passo lento (~7 min).

### 6. Abrir a demo

```bash
terraform output chat_url
terraform output dashboard_url
```

Aguarde 2 a 3 minutos após o `apply`: as instâncias ainda estão instalando o
Docker, baixando a imagem e sendo aprovadas no health check. Até lá o ALB
responde `502`, o que é esperado, não erro de configuração.

Conferir os alvos saudáveis (deve haver 3 em `healthy`):

```bash
aws elbv2 describe-target-health \
  --target-group-arn "$(terraform output -raw target_group_arn)" \
  --query 'TargetHealthDescriptions[*].[Target.Id,TargetHealth.State]' \
  --output table
```

---

## Publicar uma nova versão do código

```bash
docker buildx build --platform linux/amd64 -t "$ECR:latest" --push .

aws autoscaling start-instance-refresh \
  --auto-scaling-group-name "$(terraform output -raw asg_name)" \
  --preferences '{"MinHealthyPercentage":50,"InstanceWarmup":300}'
```

A substituição é contínua: metade dos nós permanece servindo enquanto a outra
metade é trocada — o chat não cai para publicar.

---

## Demonstração de falha (critério EC3)

1. Abra o `dashboard_url` no projetor e o `chat_url` em duas abas.
2. Liste as instâncias e escolha uma:

   ```bash
   aws autoscaling describe-auto-scaling-groups \
     --auto-scaling-group-names "$(terraform output -raw asg_name)" \
     --query 'AutoScalingGroups[0].Instances[*].[InstanceId,LifecycleState,HealthStatus]' \
     --output table
   ```

3. Derrube-a:

   ```bash
   aws ec2 terminate-instances --instance-ids i-0123456789abcdef0
   ```

4. Acompanhe o efeito no ALB, lado a lado com o painel:

   ```bash
   watch -n 5 "aws elbv2 describe-target-health \
     --target-group-arn '$(terraform output -raw target_group_arn)' \
     --query 'TargetHealthDescriptions[*].[Target.Id,TargetHealth.State]' \
     --output table"
   ```

O que se observa, e por que:

| Tempo | Evento | Causa no código desta pasta |
|---|---|---|
| ~10 s | O alvo sai do pool do ALB | `deregistration_delay = 10` no target group |
| ≤ 15 s | O `node_id` some do `/dashboard` | Sweeper do ZSET `chat:nodes` no Redis |
| ≤ 5 s | Clientes reconectam em outro nó, **sem lacuna de `seq`** | `seq` no Redis + histórico no DynamoDB, ambos fora do nó |
| ≤ 45 s | O ALB confirma o alvo doente | Health check `/readyz`: 15 s × 3 falhas |
| ~2-3 min | O ASG cria um nó novo, com `node_id` novo | `health_check_type = "ELB"` + `min_size` |

O `node_id` exibido no painel **é o próprio ID da instância EC2** (obtido via
IMDSv2 no `user_data`). Isso é deliberado: o identificador que some da tela é
literalmente o mesmo que aparece no console da AWS, sem intermediários.

---

## Custo e Free Tier

Região `us-east-1`, preços sob demanda de referência. **Estimativas** — confira
o Billing da sua conta.

| Recurso | Quantidade | Preço aprox. | Free Tier (12 meses) |
|---|---|---|---|
| EC2 `t3.micro` | 3 × 24 h | US$ 0,0104/h cada | 750 h/mês — cobre **1** instância |
| Application Load Balancer | 1 | US$ 0,0225/h + LCU | 750 h/mês + 15 LCU-h |
| ElastiCache `cache.t3.micro` | 1 | US$ 0,017/h | 750 h/mês |
| EBS gp3 8 GB | 3 | US$ 0,08/GB-mês | 30 GB-mês |
| DynamoDB on-demand | volume de demo | US$ 1,25/milhão de escritas | 25 GB de armazenamento |
| ECR | < 1 GB | US$ 0,10/GB-mês | 500 MB-mês |
| CloudWatch Logs | poucos MB | US$ 0,50/GB ingerido | 5 GB/mês |
| Gateway Endpoint (DynamoDB) | 1 | **US$ 0,00** | sempre gratuito |
| SSM Parameter Store (standard) | 1 | **US$ 0,00** | sempre gratuito |

**Custo por hora com o ambiente de pé (3 nós):** ≈ **US$ 0,07/h**.
Uma sessão de ensaio + apresentação de 4 horas custa cerca de **US$ 0,30**.

**Se o ambiente ficar ligado um mês inteiro:** ≈ US$ 50 sem Free Tier, ou
≈ US$ 15-20 com os créditos do Free Tier de 12 meses aplicados. É o cenário a
evitar.

Três pontos que costumam surpreender:

1. O Free Tier de EC2 cobre **750 horas/mês**, o equivalente a **uma** instância
   ligada o tempo todo. Com `desired_capacity = 3`, o consumo é de ~2.160 h/mês
   — as outras ~1.400 h são cobradas.
2. Contas AWS criadas a partir de meados de 2025 usam o Free Tier baseado em
   **créditos**, não em cotas por serviço. Verifique qual é o seu antes de
   deixar o ambiente ligado.
3. **Não existe NAT Gateway aqui** (ADR-006) — e essa ausência é o que mantém a
   conta em dois dígitos. Um NAT custaria ~US$ 32/mês por AZ, mais que todo o
   resto somado.

---

> ## ⚠️ AVISO — DESTRUA O AMBIENTE APÓS A APRESENTAÇÃO ⚠️
>
> **Este ambiente cobra por hora, 24 horas por dia, esteja alguém usando ou
> não.** Esquecer os recursos de pé é a única forma realista de este trabalho
> gerar uma fatura desagradável.
>
> Assim que a apresentação terminar:
>
> ```bash
> cd infra/terraform
> terraform destroy
> ```
>
> Digite `yes` e aguarde de 5 a 10 minutos. O `destroy` remove **tudo**: ALB,
> ASG e instâncias, ElastiCache, tabelas do DynamoDB (**com as mensagens**),
> repositório ECR (**com as imagens**, graças ao `force_delete`), VPC, IAM e o
> segredo no SSM.
>
> **Confira que não sobrou nada** — o `destroy` pode falhar parcialmente se algo
> tiver sido criado à mão no console:
>
> ```bash
> aws ec2 describe-instances \
>   --filters "Name=tag:Project,Values=SalaViva" "Name=instance-state-name,Values=running" \
>   --query 'Reservations[*].Instances[*].InstanceId' --output text
>
> aws elbv2 describe-load-balancers --query 'LoadBalancers[*].LoadBalancerName' --output text
> aws elasticache describe-cache-clusters --query 'CacheClusters[*].CacheClusterId' --output text
> ```
>
> As três saídas devem vir vazias. Toda a infraestrutura leva a tag
> `Project=SalaViva`, então qualquer sobra é encontrável por ela.
>
> **Precisa apenas pausar até o dia da apresentação?** Zerar a capacidade
> elimina o custo de EC2 (o maior item) sem destruir nada:
>
> ```bash
> aws autoscaling update-auto-scaling-group \
>   --auto-scaling-group-name "$(terraform output -raw asg_name)" \
>   --min-size 0 --desired-capacity 0
> ```
>
> Para retomar, volte para `--min-size 2 --desired-capacity 3`. O ALB e o
> ElastiCache continuam sendo cobrados (~US$ 0,04/h), então isso serve para
> horas ou dias, não para semanas.

---

## Solução de problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| ALB responde `502` por mais de 5 min | Nenhum alvo saudável: imagem ausente no ECR ou container não sobe | Confira o passo 4; veja os logs em `terraform output cloudwatch_log_group` |
| Instâncias sendo criadas e destruídas em laço | `/readyz` reprovando (sem Redis ou sem DynamoDB) | Verifique o SG do Redis e a policy IAM; leia `/var/log/cloud-init-output.log` na instância |
| `exec format error` no log do container | Imagem construída para arm64 (Apple Silicon) | Rebuild com `--platform linux/amd64` |
| `AccessDenied` do DynamoDB no log da app | Metadata inacessível a partir do container | `http_put_response_hop_limit` precisa ser 2 (já é, em `compute.tf`) |
| `RepositoryNotEmptyException` no destroy | `force_delete` desligado | Já está ligado em `ecr.tf`; se persistir, apague as imagens e repita |
| `plan` pede `jwt_secret` toda vez | `terraform.tfvars` não foi criado | Passo 1 |
| WebSocket cai a cada ~60 s | `idle_timeout` do ALB no padrão | Já está em 300 s em `alb.tf`; confirme que o `apply` foi aplicado |

**Logs de um nó específico:**

```bash
aws logs tail "$(terraform output -raw cloudwatch_log_group)" --follow
```

**Entrar em uma instância** (exige `allowed_ssh_cidr` + `key_pair_name`, ou
`enable_ssm_session_manager = true`):

```bash
aws ssm start-session --target i-0123456789abcdef0   # via Session Manager
sudo docker logs -f salaviva                          # já dentro da instância
```
