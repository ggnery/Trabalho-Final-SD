---
story: S-007
unit: 001-core-domain
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-007: Reordenacao de chegada fora de ordem

## Narrativa

Como cliente, quero segurar mensagens que chegam fora de ordem ate que a lacuna seja preenchida, para nunca renderizar a conversa embaralhada.

## Criterios de Aceitacao

- Chegada 3,1,2 e entregue como 1,2,3
- Mensagem duplicada e descartada
- Lacuna que persiste alem do timeout dispara pedido de resync
- Fila nao cresce indefinidamente (limite de buffer)

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
