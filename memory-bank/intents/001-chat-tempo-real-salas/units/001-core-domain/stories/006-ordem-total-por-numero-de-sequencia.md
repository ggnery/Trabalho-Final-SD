---
story: S-006
unit: 001-core-domain
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-006: Ordem total por numero de sequencia

## Narrativa

Como participante de uma sala, quero que toda mensagem receba um numero de sequencia unico e monotonico, para que todos os clientes exibam a mesma ordem.

## Criterios de Aceitacao

- `seq` e estritamente crescente e sem repeticao dentro de uma sala
- `seq` e atribuido antes de qualquer difusao
- Duas salas tem sequencias independentes

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
