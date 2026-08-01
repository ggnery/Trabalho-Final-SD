---
story: S-010
unit: 002-messaging-infra
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-010: Presenca por sorted set com heartbeat

## Narrativa

Como participante, quero ver quem esta online na sala, com remocao automatica de quem caiu.

## Criterios de Aceitacao

- ZADD com score = epoch do heartbeat
- Sweeper remove score mais antigo que 15s
- Entrada/saida emite `presence_update`

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
