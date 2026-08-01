---
story: S-013
unit: 002-messaging-infra
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-013: Consultar backlog por last_seq

## Narrativa

Como cliente que reconecta, quero receber exatamente as mensagens que perdi.

## Criterios de Aceitacao

- Query com KeyCondition room_id = :r AND seq > :last
- Retorno ja ordenado por seq crescente
- Leitura fortemente consistente
- Limite de itens com paginacao

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
