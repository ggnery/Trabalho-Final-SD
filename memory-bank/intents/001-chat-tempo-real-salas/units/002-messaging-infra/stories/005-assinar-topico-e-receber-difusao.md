---
story: S-005
unit: 002-messaging-infra
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-005: Assinar topico e receber difusao

## Narrativa

Como no do cluster, quero assinar os topicos das salas que meus clientes ocupam, para entregar a eles mensagens originadas em qualquer no.

## Criterios de Aceitacao

- Assina ao primeiro membro local entrar; cancela ao ultimo sair
- Reconexao ao Redis com backoff exponencial + jitter (0,5s -> 30s)
- Perda de conexao com o Redis reprova `/readyz`

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
