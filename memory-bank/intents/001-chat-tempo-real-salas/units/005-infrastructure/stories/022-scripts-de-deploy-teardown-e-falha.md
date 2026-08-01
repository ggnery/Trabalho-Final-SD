---
story: S-022
unit: 005-infrastructure
intent: 001-chat-tempo-real-salas
status: complete
priority: Must
created: 2026-08-01T16:30:00Z
updated: 2026-08-01T14:45:00Z
---

# S-022: Scripts de deploy, teardown e falha

## Narrativa

Como apresentador, quero derrubar um no com um comando durante a demo.

## Criterios de Aceitacao

- `kill_node.sh` termina uma instancia EC2 do ASG
- `watch_cluster.sh` acompanha nos e mensagens ao vivo
- `teardown.sh` destroi tudo e evita custo residual

## Notas

Ver `memory-bank/standards/system-architecture.md` e `memory-bank/standards/decision-index.md` para as decisoes que restringem esta story.
