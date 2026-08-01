---
story: S-011
unit: 003-websocket-gateway
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-011: Autenticacao por JWT no handshake

## Narrativa

Como sistema, quero autenticar a conexao antes de aceita-la.

## Criterios de Aceitacao

- Token ausente/invalido/expirado fecha com codigo 4401
- `sub` do token vincula a sessao ao usuario
- Validacao stateless: qualquer no valida sem estado compartilhado

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
