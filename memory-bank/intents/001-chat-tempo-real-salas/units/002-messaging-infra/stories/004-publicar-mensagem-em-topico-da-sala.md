---
story: S-004
unit: 002-messaging-infra
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-004: Publicar mensagem em topico da sala

## Narrativa

Como no do cluster, quero publicar a mensagem em um topico Redis da sala, para difundi-la sem conhecer os demais nos.

## Criterios de Aceitacao

- PUBLISH em `chat:room:{room_id}`
- Publicador nao conhece a identidade de nenhum assinante
- Envelope serializado em JSON valido

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
