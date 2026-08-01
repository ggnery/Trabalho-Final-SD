---
story: S-014
unit: 003-websocket-gateway
intent: 001-chat-tempo-real-salas
status: complete
priority: Should
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-014: Idempotencia por client_msg_id

## Narrativa

Como usuario com rede instavel, quero reenviar sem duplicar a mensagem.

## Criterios de Aceitacao

- SET NX em `chat:dedupe:{client_msg_id}` com TTL de 300s
- Reenvio retorna o `ack` original, sem novo `seq`

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
