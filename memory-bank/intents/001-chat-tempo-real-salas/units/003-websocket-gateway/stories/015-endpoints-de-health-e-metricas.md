---
story: S-015
unit: 003-websocket-gateway
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-015: Endpoints de health e metricas

## Narrativa

Como operador, quero que o ALB retire do pool o no degradado.

## Criterios de Aceitacao

- `/healthz` = liveness do processo
- `/readyz` verifica Redis e DynamoDB; e o alvo do health check do ALB
- `/metrics` retorna node_id, conexoes, salas, msgs publicadas/recebidas, uptime

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
