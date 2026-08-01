# Deploy na AWS Academy Sandbox

Runbook completo para subir o SalaViva na sandbox da AWS Academy — o ambiente
com orçamento de **US$ 20** e serviços restritos.

> Se você tem uma conta AWS comum (não a da Academy), **use `infra/terraform/`**,
> não esta pasta. Aquela é a infraestrutura de referência, é a documentada no
> SDD, e é a que o professor avalia como arquitetura.

---

## Por que existe uma variante

A sandbox não libera quatro coisas que a arquitetura de referência usa:

| Referência (`infra/terraform/`) | Sandbox | Substituto aqui |
|---|---|---|
| `aws_elasticache_cluster` | ElastiCache **não liberado** | Redis num EC2 dedicado |
| `aws_ecr_repository` | ECR **não liberado** | `git clone` + `docker build` na instância |
| `aws_iam_role` / `_policy` / `_instance_profile` | IAM **read-only** | Usa a `LabInstanceProfile` já existente |
| `aws_ssm_parameter` (SecureString) | KMS **só listagem** | Segredo via `user_data` |

**O comportamento do sistema não muda.** Continua sendo Pub/Sub para difusão,
`INCR` para ordem total e ZSET para presença — os três recursos que o SalaViva
usa do Redis existem igualmente no Redis de código aberto. O que muda é quem
hospeda o Redis e de onde vem a imagem.

Duas divergências que você deve **declarar na apresentação** se perguntarem:

1. **O segredo JWT vai no `user_data`**, não em SSM SecureString. Fica legível
   para quem tiver `ec2:DescribeInstanceAttribute` na conta. Aceitável em
   ambiente acadêmico, inaceitável em produção — e a restrição do ambiente é o
   motivo, não a conveniência.
2. **O Redis é ponto único de falha** e não tem backup gerenciado. Na referência
   também é single-node (ADR-002), mas lá a mitigação de produção é uma flag do
   ElastiCache; aqui exigiria montar replicação manualmente.

---

## Pré-requisitos

Na sua máquina:

```bash
brew install awscli
brew install hashicorp/tap/terraform
```

Não precisa de Docker local para o deploy — quem constrói a imagem é a própria
instância.

---

## Atalho: o ciclo em um comando

A sessão da sandbox expira a cada ~3 horas e o ciclo saudável é subir e destruir
a cada uma. Para isso não custar dez passos:

```bash
make sandbox-status    # credenciais válidas? o que está no ar? consumindo crédito?
make sandbox-up        # apply + espera os nós ficarem saudáveis + imprime as URLs
make sandbox-down      # destrói tudo e confere se sobrou algo
```

Se as credenciais tiverem expirado, o script diz exatamente o que fazer para
renovar. **Nada se perde quando a sessão acaba** — é só renovar e subir de novo.

O passo a passo manual, abaixo, continua valendo e é o que os comandos acima
automatizam.

---

## 1. Iniciar o laboratório

No Vocareum:

1. **Start Lab** — o indicador ao lado de "AWS" vai de vermelho → amarelo → **verde**.
2. Anote quanto do orçamento já foi usado (`Used $X of $20`, no topo).

## 2. Pegar as credenciais

Clique em **AWS Details** → **AWS CLI** → **Show**. Vai aparecer um bloco assim:

```ini
[default]
aws_access_key_id=ASIA...
aws_secret_access_key=...
aws_session_token=...
```

Cole em `~/.aws/credentials`.

> **As três linhas são obrigatórias.** São credenciais temporárias; sem o
> `aws_session_token` toda chamada falha com `InvalidClientTokenId`. E elas
> **expiram quando a sessão do laboratório termina** — ao retomar noutro dia,
> repita este passo.

Confirme:

```bash
aws sts get-caller-identity
```

## 3. Verificar a instance profile

```bash
aws iam list-instance-profiles --query 'InstanceProfiles[].InstanceProfileName' --output text
```

Se aparecer `LabInstanceProfile`, siga em frente. Se **não** aparecer nenhuma,
deixe `instance_profile = ""` no passo seguinte — a persistência no DynamoDB
será desligada e o chat funcionará sem replay de histórico.

## 4. Configurar

```bash
cd infra/terraform-sandbox
cp terraform.tfvars.example terraform.tfvars
openssl rand -base64 32          # cole o resultado em jwt_secret
```

Edite `terraform.tfvars`. A única variável obrigatória é `jwt_secret`.

## 5. Aplicar

```bash
terraform init
terraform plan       # leia a saída antes de aplicar
terraform apply
```

Leva **10 a 15 minutos**. A maior parte é as instâncias clonando o repositório e
construindo a imagem Docker — a `t3.micro` não é rápida nisso.

```bash
terraform output chat_url
```

## 6. Verificar

```bash
# repita até dar 3 (leva alguns minutos após o apply terminar)
curl -s "$(terraform output -raw chat_url)/api/nodes" | python3 -m json.tool
```

Abra a `chat_url` no navegador. Abra em **duas abas** com nomes diferentes: o
cabeçalho mostra o `node_id` de cada uma, e serão `i-0abc...` diferentes —
instâncias EC2 distintas trocando mensagens pelo Redis.

---

## Demonstração de falha (critério EC3)

```bash
export SALAVIVA_LB_URL="$(terraform output -raw chat_url)"
export SALAVIVA_ASG_NAME="$(terraform output -raw asg_name)"

cd ../..
./scripts/kill_node.sh --aws
```

O script escolhe uma instância `InService`, termina, e cronometra: quando o nó
some do `/api/nodes`, quando o ALB o remove do pool e quando o substituto entra
em serviço.

Deixe o `/dashboard` projetado — o nó derrubado desaparece do painel em até 15 s.

A prova de que nada se perdeu:

```bash
curl -s "$SALAVIVA_LB_URL/api/rooms/geral/messages" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('contiguo:', d['contiguous'], '· mensagens:', d['count'])"
```

> **Atenção ao tempo de reposição.** Se você deixou o build na instância, o nó
> substituto leva **4 a 6 minutos** para entrar em serviço — tempo demais para
> uma apresentação de 15 minutos. Publique a imagem no Docker Hub e defina
> `imagem_docker` no `terraform.tfvars`; a reposição cai para ~1 minuto:
>
> ```bash
> docker buildx build --platform linux/amd64 -t SEU-USUARIO/salaviva:latest --push .
> ```
>
> O `--platform linux/amd64` é obrigatório se você constrói num Mac Apple
> Silicon: as instâncias são x86 e a imagem ARM não executa.

---

## Encerrar a sessão — leia isto

O orçamento é de **US$ 20 no total, sem reposição**. Se acabar, a conta é
bloqueada e **todos os recursos são apagados permanentemente**.

Este ambiente consome cerca de **US$ 0,07 por hora**: 3 nós + 1 Redis
(4 × `t3.micro`) mais o ALB. Deixá-lo de pé por uma semana consome mais da
metade do crédito.

Ao terminar **cada** sessão de estudo:

```bash
cd infra/terraform-sandbox
terraform destroy        # digite 'yes'
```

Depois, **End Lab** no Vocareum.

> **Por que destruir e não só dar End Lab?** O End Lab para as instâncias, mas o
> ALB continua contando e o EBS segue cobrando centavos. Pior: ao reiniciar o
> laboratório, o Auto Scaling encontra instâncias paradas, considera-as doentes
> e as substitui — você paga pela criação de novas sem ter ganho nada.
> `terraform destroy` deixa a conta limpa, e o `apply` recria tudo em 15 min.

Confira que nada sobrou:

```bash
aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=SalaViva" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text
aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerName' --output text
```

---

## Retomar numa nova sessão

1. **Start Lab**, espere o verde.
2. **Atualize as credenciais** em `~/.aws/credentials` (as antigas expiraram).
3. `terraform apply` — recria tudo.

O arquivo `terraform.tfstate` fica na sua máquina e sobrevive entre sessões. Se
você destruiu ao final da sessão anterior, o state estará vazio e o `apply` cria
do zero. Se **não** destruiu, o Terraform vai reconciliar o que ainda existe.

---

## Solução de problemas

**`InvalidClientTokenId` ou `ExpiredToken`**
Credenciais expiraram. Refaça o passo 2.

**Os nós nunca ficam saudáveis (`/api/nodes` sempre vazio)**
Provavelmente o build falhou. Conecte na instância pelo console (EC2 → Connect →
EC2 Instance Connect) e veja:

```bash
sudo tail -100 /var/log/salaviva-boot.log
sudo docker ps -a
sudo docker logs salaviva
```

Causas comuns: repositório privado (precisa ser público), branch inexistente, ou
falta de memória no build (o `user_data` já cria 2 GiB de swap; se ainda faltar,
use `tipo_instancia = "t3.small"`).

**O chat funciona mas o histórico some ao reconectar**
A persistência está desligada. Confirme com
`terraform output persistencia_ativa`. Se der `false`, a `instance_profile` está
vazia ou o nome está errado — refaça o passo 3.

**`UnauthorizedOperation` no apply**
Você tentou criar algo fora dos serviços liberados. Confira a mensagem: se citar
IAM, ElastiCache, ECR ou KMS, provavelmente você está aplicando
`infra/terraform/` por engano, e não esta pasta.

**Estouro do limite de instâncias**
A sandbox permite 9. Este ambiente usa `nos_desejados + 1`. Se você tiver outros
laboratórios de pé, reduza `nos_desejados`.

---

## Custo estimado

| Recurso | Quantidade | US$/hora aprox. |
|---|---|---|
| EC2 `t3.micro` (nós) | 3 | 0,031 |
| EC2 `t3.micro` (Redis) | 1 | 0,010 |
| Application Load Balancer | 1 | 0,023 |
| DynamoDB On-Demand | 2 tabelas | ~0 no volume da demo |
| EBS gp3 20 GB | 4 | 0,002 |
| **Total** | | **≈ 0,066 / hora** |

Com US$ 20, isso dá cerca de **300 horas** — mas só se você destruir entre as
sessões. Ensaiar a apresentação por 3 horas custa ~US$ 0,20; esquecer ligado por
duas semanas custa ~US$ 22 e zera sua conta.
