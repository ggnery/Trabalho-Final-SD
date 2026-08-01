# ---------------------------------------------------------------------------
# versions.tf — versões, provider e tags padrão
#
# Este arquivo fixa o contrato de compatibilidade da IaC. As restrições de
# versão são intencionalmente estreitas: um `terraform apply` feito pelo
# professor meses depois precisa produzir a mesma infraestrutura que o nosso.
# ---------------------------------------------------------------------------

terraform {
  # >= 1.5 porque usamos blocos `check`-friendly, `validation` em variáveis com
  # `can()` e a sintaxe moderna de `import`. Não fixamos um teto porque o
  # Terraform mantém compatibilidade retroativa na série 1.x.
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # ~> 5.0 permite correções e novos recursos dentro da major 5, mas barra a
      # major 6 — que traz mudanças incompatíveis (ex.: remoção de argumentos
      # inline de Security Group). Sem esse teto, o mesmo código quebraria
      # sozinho no futuro.
      version = "~> 5.0"
    }
  }

  # ---------------------------------------------------------------------------
  # Estado: local (terraform.tfstate na própria pasta).
  #
  # Para um trabalho acadêmico com um operador por vez, backend local é o
  # correto: zero infraestrutura extra, zero custo, e o `terraform destroy`
  # depois da apresentação não depende de nenhum bucket sobreviver.
  # Em produção (ou com o time aplicando em paralelo) o estado iria para S3 com
  # travamento em DynamoDB, como no bloco abaixo.
  #
  # backend "s3" {
  #   bucket         = "salaviva-tfstate"
  #   key            = "prod/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "salaviva-tflock"
  #   encrypt        = true
  # }
  # ---------------------------------------------------------------------------
}

provider "aws" {
  region = var.aws_region

  # Tags aplicadas automaticamente a todo recurso que suporta tags. Servem para
  # duas coisas concretas neste projeto: filtrar o custo no Cost Explorer
  # (Project=SalaViva) e permitir auditar, depois da apresentação, se sobrou
  # algum recurso criado pelo Terraform que o `destroy` não removeu.
  default_tags {
    tags = {
      Project   = "SalaViva"
      ManagedBy = "Terraform"
    }
  }
}
