---
intent: 001-chat-tempo-real-salas
artifact: inception-log
created: 2026-08-01T16:20:00Z
updated: 2026-08-01T16:35:00Z
---

# Inception Log — Chat em Tempo Real com Salas

## 2026-08-01T16:20:00Z — Inicialização do projeto

Projeto criado a partir do **Projeto 3** da proposta da disciplina de Sistemas Distribuídos (`orientacoes/Proposta_Disciplina_SD.pdf`). Tipo definido como `full-stack-web`.

Restrição de entrada do time: **backend em Python**, por simplicidade.

## 2026-08-01T16:20:00Z — Decisão de arquitetura (checkpoint com o time)

Quatro decisões colhidas e travadas:

1. **Arquitetura**: EC2 + ALB + ElastiCache Redis + DynamoDB, em vez de API Gateway WebSocket + Lambda.
   *Motivo*: o critério de avaliação EC3 exige simulação de falha derrubando uma instância EC2. Serverless não expõe instância derrubável, o que tornaria a demonstração de tolerância a falhas uma afirmação sobre a AWS em vez de uma propriedade do sistema construído. Registrado em ADR-001.
2. **Acesso AWS**: conta própria com Free Tier → liberdade de IAM, dimensionamento restrito ao Free Tier, teardown obrigatório.
3. **Cliente**: web (demo visual) **e** CLI (evidência técnica de relógios lógicos e concorrência).
4. **Entregáveis**: SDD, IaC em Terraform, testes automatizados + carga, roteiro de demo com script de falha.

## 2026-08-01T16:20:00Z — Standards aprovados

Criados quatro standards em `memory-bank/standards/`: `tech-stack`, `data-stack`, `coding-standards`, `system-architecture`, mais o `decision-index` com **8 ADRs**.

Decisões estruturais registradas, todas com trade-off explícito — a justificativa é o critério EC1 da avaliação, então cada escolha carrega o motivo e a alternativa descartada:

- ADR-001: EC2/ASG em vez de serverless
- ADR-002: Redis Pub/Sub em vez de SNS/SQS (latência e semântica de fan-out)
- ADR-003: ordem total por `INCR` atômico, nem consenso nem timestamp físico
- ADR-004: emissor recebe a própria mensagem pelo Pub/Sub (caminho único de entrega)
- ADR-005: Lamport **e** relógio vetorial, com papéis distintos
- ADR-006: EC2 em subrede pública, sem NAT Gateway (custo)
- ADR-007: sticky sessions no ALB
- ADR-008: persistência assíncrona fora do caminho crítico

## 2026-08-01T16:25:00Z — Requisitos elaborados

**13 requisitos funcionais** e NFRs em cinco categorias (performance, escalabilidade, segurança, confiabilidade, conformidade de custo), todos com métrica e alvo mensuráveis.

Tabela de rastreabilidade requisito → critério de avaliação adicionada ao final de `requirements.md`: cada um dos três critérios da disciplina (EC1 documentação, EC2 conceitos, EC3 demonstração) tem os requisitos que o atendem explicitamente mapeados.

Três questões em aberto; duas resolvidas na hora (região `us-east-1`; HTTP no ALB para a demo, com caminho para HTTPS documentado). **Uma permanece aberta**: nome do projeto, integrantes, professor e data para o Slide 1 — os slides usam placeholders `{{...}}` até que o time forneça.

## 2026-08-01T16:30:00Z — Decomposição em unidades

Sete unidades, com o critério de corte sendo a **direção da dependência da arquitetura hexagonal** (núcleo puro → adaptadores → borda → consumidores da borda). O efeito prático é que `004-clients` e `005-infrastructure` ficam sem dependência mútua e podem ser construídas em paralelo.

**29 stories** distribuídas, cada uma com narrativa e critérios de aceitação binários.

## 2026-08-01T16:35:00Z — Planejamento de bolts

Sete bolts, um por unidade. Caminho crítico: 001 → 002 → 003. Os bolts 004 e 005 executam em paralelo após 003; 006 valida o conjunto; 007 documenta o que foi provado.

Tipo `ddd-construction-bolt` para as unidades com modelagem de domínio relevante (001, 002, 003, 006) e `simple-construction-bolt` para as demais.

**Inception concluída.** → Construction Agent.
