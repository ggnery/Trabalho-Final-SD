---
story: S-003
unit: 003-websocket-gateway
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-003: Sair de sala

## Narrativa

Como usuario, quero sair de uma sala sem derrubar minha conexao.

## Criterios de Aceitacao

- `leave` remove da presenca e emite `presence_update`
- Conexao permanece aberta para as demais salas
- Ultimo membro local a sair cancela a assinatura do topico

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
