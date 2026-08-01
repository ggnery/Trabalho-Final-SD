---
story: S-026
unit: 006-quality
intent: 001-chat-tempo-real-salas
status: complete
priority: Should
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-026: Teste de carga com grafico

## Narrativa

Como time, quero numeros de escalabilidade para os slides.

## Criterios de Aceitacao

- >= 1000 conexoes WebSocket concorrentes
- Mede p50/p95/p99 de latencia fim a fim
- Verifica ordem sob carga
- Gera grafico PNG para a apresentacao

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
