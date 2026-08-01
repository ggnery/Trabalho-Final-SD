---
id: 001-core-domain
unit: 001-core-domain
intent: 001-chat-tempo-real-salas
type: ddd-construction-bolt
status: complete
stories:
  - S-006
  - S-007
  - S-008
  - S-009
created: 2026-08-01T16:35:00Z
started: 2026-08-01T16:40:00Z
completed: 2026-08-01T14:45:00Z
current_stage: complete
stages_completed: []

requires_bolts: []
enables_bolts: ['002-messaging-infra']
requires_units: []
blocks: false

complexity:
  avg_complexity: 2
  avg_uncertainty: 1
  max_dependencies: 0
  testing_scope: 1
---

# Bolt: 001-core-domain

## Overview

Implementar o nucleo puro: relogios logicos de Lamport e vetorial, envelope de mensagem, fila de hold-back e as interfaces (ports) que os adaptadores implementam.

## Objective

Estabelecer a fundacao algoritmica e os contratos. Nenhuma outra unidade pode comecar antes: todas dependem dos tipos e das portas definidos aqui.

## Stories Included

- **S-006**
- **S-007**
- **S-008**
- **S-009**

## Bolt Type

**Type**: ddd-construction-bolt
**Definition**: `.specsmd/aidlc/templates/construction/bolt-types/ddd-construction-bolt.md`

## Stages

- OK **1. model**: Complete -> ddd-01-domain-model.md
- OK **2. design**: Complete -> ddd-02-technical-design.md
- OK **3. implement**: Complete -> codigo-fonte
- OK **4. test**: Complete -> ddd-03-test-report.md

## Dependencies

### Requires
- None (bolt de fundacao)

### Enables
- 002-messaging-infra

## Success Criteria

- OK Todas as stories implementadas
- OK Todos os criterios de aceitacao atendidos
- OK Testes passando
- OK Conforme `memory-bank/standards/coding-standards.md`

## Notes

Restricoes arquiteturais aplicaveis em `memory-bank/standards/decision-index.md`.
