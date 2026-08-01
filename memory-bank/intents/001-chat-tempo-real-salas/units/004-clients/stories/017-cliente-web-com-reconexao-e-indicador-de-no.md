---
story: S-017
unit: 004-clients
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-017: Cliente web com reconexao e indicador de no

## Narrativa

Como apresentador, quero uma UI que mostre a qual no estou conectado e que se recupere sozinha da queda.

## Criterios de Aceitacao

- Sem etapa de build; servida pelo backend
- Exibe node_id da conexao atual
- Reconecta com backoff e retoma de last_seq
- Lista de presenca em tempo real

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
