---
story: S-012
unit: 002-messaging-infra
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-012: Persistir mensagem no historico

## Narrativa

Como sistema, quero gravar cada mensagem no DynamoDB, para permitir replay apos falha.

## Criterios de Aceitacao

- PutItem com PK=room_id, SK=seq
- Gravacao assincrona, fora do caminho critico (ADR-008)
- Retry com backoff; falha nao interrompe a entrega em tempo real
- TTL de 7 dias

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
