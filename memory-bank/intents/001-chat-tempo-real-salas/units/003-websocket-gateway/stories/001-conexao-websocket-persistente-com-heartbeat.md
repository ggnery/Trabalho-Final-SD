---
story: S-001
unit: 003-websocket-gateway
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-001: Conexao WebSocket persistente com heartbeat

## Narrativa

Como usuario, quero manter uma conexao aberta e receber mensagens em push.

## Criterios de Aceitacao

- `/ws?token=...` aceita e mantem a conexao
- Ping a cada 20s; fecha apos 2 pings sem pong
- Cada conexao roda em Task propria: erro em uma nao afeta as outras

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
