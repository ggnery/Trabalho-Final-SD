---
story: S-025
unit: 006-quality
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-025: Teste e2e de falha de no

## Narrativa

Como avaliador, quero a prova de que derrubar um no nao perde mensagem.

## Criterios de Aceitacao

- No morre no meio do fluxo
- Cliente reconecta com last_seq e recebe exatamente a lacuna
- Contagem final identica entre todos os clientes

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
