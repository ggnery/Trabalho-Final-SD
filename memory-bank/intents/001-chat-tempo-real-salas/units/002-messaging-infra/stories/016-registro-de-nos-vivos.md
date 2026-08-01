---
story: S-016
unit: 002-messaging-infra
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-016: Registro de nos vivos

## Narrativa

Como avaliador, quero ver quais nos estao vivos, para constatar a falha e a recuperacao.

## Criterios de Aceitacao

- ZADD em `chat:nodes` a cada 5s
- No sem heartbeat ha mais de 15s some do registro
- Registro alimenta `/dashboard`

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
