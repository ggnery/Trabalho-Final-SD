---
story: S-002
unit: 003-websocket-gateway
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-002: Entrar em sala

## Narrativa

Como usuario, quero entrar em uma sala e receber membros e historico.

## Criterios de Aceitacao

- `join` cria a sala se nao existir
- Resposta traz membros + backlog a partir de last_seq
- Um socket pode ocupar >= 5 salas

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
