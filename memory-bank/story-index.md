---
artifact: story-index
mode: single-file
intent: 001-chat-tempo-real-salas
created: 2026-08-01T16:35:00Z
updated: 2026-08-01T14:45:00Z
total_stories: 29
---

# Story Index — SalaViva

| Story | Título | Unit | Bolt | Prioridade | FR | Status |
|---|---|---|---|---|---|---|
| S-001 | Conexão WebSocket persistente com heartbeat | 003-websocket-gateway | 003 | Must | FR-2 | complete |
| S-002 | Entrar em sala | 003-websocket-gateway | 003 | Must | FR-3 | complete |
| S-003 | Sair de sala | 003-websocket-gateway | 003 | Must | FR-3 | complete |
| S-004 | Publicar mensagem em tópico da sala | 002-messaging-infra | 002 | Must | FR-4 | complete |
| S-005 | Assinar tópico e receber difusão | 002-messaging-infra | 002 | Must | FR-4 | complete |
| S-006 | Ordem total por número de sequência | 001-core-domain | 001 | Must | FR-5 | complete |
| S-007 | Reordenação de chegada fora de ordem | 001-core-domain | 001 | Must | FR-5 | complete |
| S-008 | Relógio de Lamport | 001-core-domain | 001 | Must | FR-6 | complete |
| S-009 | Relógio vetorial e detecção de concorrência | 001-core-domain | 001 | Should | FR-6 | complete |
| S-010 | Presença por sorted set com heartbeat | 002-messaging-infra | 002 | Must | FR-7 | complete |
| S-011 | Autenticação por JWT no handshake | 003-websocket-gateway | 003 | Must | FR-1 | complete |
| S-012 | Persistir mensagem no histórico | 002-messaging-infra | 002 | Must | FR-8 | complete |
| S-013 | Consultar backlog por `last_seq` | 002-messaging-infra | 002 | Must | FR-8 | complete |
| S-014 | Idempotência por `client_msg_id` | 003-websocket-gateway | 003 | Should | FR-9 | complete |
| S-015 | Endpoints de health e métricas | 003-websocket-gateway | 003 | Must | FR-10 | complete |
| S-016 | Registro de nós vivos | 002-messaging-infra | 002 | Must | FR-10 | complete |
| S-017 | Cliente web com reconexão e indicador de nó | 004-clients | 004 | Must | FR-11 | complete |
| S-018 | Cliente CLI com visualização de relógios | 004-clients | 004 | Should | FR-12 | complete |
| S-019 | Rate limiting por sessão | 003-websocket-gateway | 003 | Should | FR-13 | complete |
| S-020 | Imagem Docker e Compose com 3 nós | 005-infrastructure | 005 | Must | — | complete |
| S-021 | Terraform da infraestrutura AWS | 005-infrastructure | 005 | Must | — | complete |
| S-022 | Scripts de deploy, teardown e falha | 005-infrastructure | 005 | Must | — | complete |
| S-023 | Testes unitários do domínio | 006-quality | 006 | Must | — | complete |
| S-024 | Teste de integração multi-nó | 006-quality | 006 | Must | FR-5 | complete |
| S-025 | Teste e2e de falha de nó | 006-quality | 006 | Must | FR-8 | complete |
| S-026 | Teste de carga com gráfico | 006-quality | 006 | Should | — | complete |
| S-027 | SDD completo | 007-deliverables | 007 | Must | — | complete |
| S-028 | Slides no template da disciplina | 007-deliverables | 007 | Must | — | complete |
| S-029 | Roteiro de demonstração | 007-deliverables | 007 | Must | — | complete |

## Distribuição

- **Must**: 22 · **Should**: 7 · **Could**: 0
- Por unit: 001 (4) · 002 (6) · 003 (7) · 004 (2) · 005 (3) · 006 (4) · 007 (3)
