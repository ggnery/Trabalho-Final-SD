---
story: S-019
unit: 003-websocket-gateway
intent: 001-chat-tempo-real-salas
status: complete
priority: Should
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-019: Rate limiting por sessao

## Narrativa

Como sistema, quero conter sessao abusiva sem afetar as demais.

## Criterios de Aceitacao

- Token bucket de 20 msg/s por sessao
- Excedente responde `error/rate_limited` sem fechar a conexao
- Outras sessoes seguem sem degradacao

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
