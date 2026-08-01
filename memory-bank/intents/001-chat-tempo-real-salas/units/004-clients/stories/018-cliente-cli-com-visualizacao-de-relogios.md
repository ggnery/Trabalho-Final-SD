---
story: S-018
unit: 004-clients
intent: 001-chat-tempo-real-salas
status: complete
priority: Should
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-018: Cliente CLI com visualizacao de relogios

## Narrativa

Como avaliador, quero ver seq, lamport e node_id por mensagem e identificar concorrencia.

## Criterios de Aceitacao

- Exibe [seq | L=n | node] por mensagem
- Marca mensagens concorrentes
- Modo `--observer` somente leitura para projecao

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
