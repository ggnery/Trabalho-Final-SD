---
story: S-008
unit: 001-core-domain
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-008: Relogio de Lamport

## Narrativa

Como avaliador, quero ver o relogio logico de Lamport implementado fielmente, para verificar a relacao happened-before entre eventos.

## Criterios de Aceitacao

- Evento local/envio: L := L+1
- Recebimento: L := max(L, L_msg) + 1
- L nunca regride
- Se a -> b entao L(a) < L(b)
- Desempate deterministico por (lamport, node_id)

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
