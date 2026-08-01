---
id: 004-clients
unit: 004-clients
intent: 001-chat-tempo-real-salas
type: simple-construction-bolt
status: complete
stories:
  - S-017
  - S-018
created: 2026-08-01T16:35:00Z
started: 2026-08-01T16:40:00Z
completed: 2026-08-01T14:45:00Z
current_stage: complete
stages_completed: []

requires_bolts: ['003-websocket-gateway']
enables_bolts: ['006-quality']
requires_units: []
blocks: false

complexity:
  avg_complexity: 2
  avg_uncertainty: 1
  max_dependencies: 1
  testing_scope: 1
---

# Bolt: 004-clients

## Overview

Construir o cliente web (sem build step, servido pelo backend) e o cliente CLI que evidencia relogios logicos e concorrencia.

## Objective

Dar rosto a demonstracao. Clareza vale 20% da nota de apresentacao.

## Stories Included

- **S-017**
- **S-018**

## Bolt Type

**Type**: simple-construction-bolt
**Definition**: `.specsmd/aidlc/templates/construction/bolt-types/simple-construction-bolt.md`

## Stages

- OK **1. design**: Complete -> design-notes.md
- OK **2. implement**: Complete -> codigo-fonte
- OK **3. test**: Complete -> test-report.md

## Dependencies

### Requires
- 003-websocket-gateway

### Enables
- 006-quality

## Success Criteria

- OK Todas as stories implementadas
- OK Todos os criterios de aceitacao atendidos
- OK Testes passando
- OK Conforme `memory-bank/standards/coding-standards.md`

## Notes

Restricoes arquiteturais aplicaveis em `memory-bank/standards/decision-index.md`.
