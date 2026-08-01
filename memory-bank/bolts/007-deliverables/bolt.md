---
id: 007-deliverables
unit: 007-deliverables
intent: 001-chat-tempo-real-salas
type: simple-construction-bolt
status: complete
stories:
  - S-027
  - S-028
  - S-029
created: 2026-08-01T16:35:00Z
started: 2026-08-01T16:40:00Z
completed: 2026-08-01T14:45:00Z
current_stage: complete
stages_completed: []

requires_bolts: ['006-quality']
enables_bolts: []
requires_units: []
blocks: false

complexity:
  avg_complexity: 2
  avg_uncertainty: 1
  max_dependencies: 3
  testing_scope: 1
---

# Bolt: 007-deliverables

## Overview

Produzir o SDD, os 10 slides no template da disciplina e o roteiro cronometrado da demonstracao.

## Objective

Entregar os artefatos avaliados que nao sao codigo. O SDD e o criterio EC1 inteiro.

## Stories Included

- **S-027**
- **S-028**
- **S-029**

## Bolt Type

**Type**: simple-construction-bolt
**Definition**: `.specsmd/aidlc/templates/construction/bolt-types/simple-construction-bolt.md`

## Stages

- OK **1. design**: Complete -> design-notes.md
- OK **2. implement**: Complete -> codigo-fonte
- OK **3. test**: Complete -> test-report.md

## Dependencies

### Requires
- 006-quality

### Enables
- Entrega final

## Success Criteria

- OK Todas as stories implementadas
- OK Todos os criterios de aceitacao atendidos
- OK Testes passando
- OK Conforme `memory-bank/standards/coding-standards.md`

## Notes

Restricoes arquiteturais aplicaveis em `memory-bank/standards/decision-index.md`.
