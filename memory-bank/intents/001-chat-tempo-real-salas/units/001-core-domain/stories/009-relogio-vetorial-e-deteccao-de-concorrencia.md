---
story: S-009
unit: 001-core-domain
intent: 001-chat-tempo-real-salas
status: complete
priority: Should
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-009: Relogio vetorial e deteccao de concorrencia

## Narrativa

Como avaliador, quero que o sistema distinga eventos causalmente relacionados de eventos concorrentes, para evidenciar a limitacao do relogio escalar.

## Criterios de Aceitacao

- V[self] incrementa a cada evento local
- Merge no recebimento usa max componente a componente
- compare() retorna BEFORE, AFTER, EQUAL ou CONCURRENT
- Eventos concorrentes sao corretamente classificados como incomparaveis

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
