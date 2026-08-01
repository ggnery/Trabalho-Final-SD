---
story: S-020
unit: 005-infrastructure
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-020: Imagem Docker e Compose com 3 nos

## Narrativa

Como time, quero reproduzir o cluster localmente com paridade.

## Criterios de Aceitacao

- 3 nos + Redis + balanceador sobem com um comando
- Mesma imagem usada local e na AWS
- `make up` funciona em maquina limpa

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
