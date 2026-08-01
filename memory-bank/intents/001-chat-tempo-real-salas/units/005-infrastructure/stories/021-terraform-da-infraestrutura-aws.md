---
story: S-021
unit: 005-infrastructure
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-021: Terraform da infraestrutura AWS

## Narrativa

Como time, quero provisionar tudo de forma reproduzivel.

## Criterios de Aceitacao

- VPC 2 AZs, ALB, ASG (min 2, max 4), ElastiCache, DynamoDB, ECR, IAM
- `terraform apply` cria o ambiente do zero
- Outputs incluem a URL do ALB
- Security Groups encadeados conforme system-architecture

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
