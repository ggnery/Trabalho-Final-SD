---
unit: 003-websocket-gateway
intent: 001-chat-tempo-real-salas
status: complete
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T16:30:00Z
story_count: 7
---

# Unit Brief: 003-websocket-gateway

Ver `memory-bank/intents/001-chat-tempo-real-salas/units.md` para descricao, entregaveis e dependencias.

## Stories

- **S-001**: Conexao WebSocket persistente com heartbeat (Must)
- **S-002**: Entrar em sala (Must)
- **S-003**: Sair de sala (Must)
- **S-011**: Autenticacao por JWT no handshake (Must)
- **S-014**: Idempotencia por client_msg_id (Should)
- **S-015**: Endpoints de health e metricas (Must)
- **S-019**: Rate limiting por sessao (Should)
