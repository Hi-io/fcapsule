# Documentation

## Operate the Current Product

- [Quickstart and scope](../README.md)
- [Operations, Targets, Patterns and Settings](operations.md)
- [Kubernetes installation and development workflow](kubernetes_deployment.md)
- [HTTPS and microphone access](https_access.md)
- [Privacy, credentials, capture storage and retention](data_privacy.md)
- [Current product requirements](../FCAPSule_AI_Project_Guide.md)

## Understand the Implementation

- [Architecture](architecture.md) and [engineering design](../PROJECT_DESIGN.md)
- [AI investigation techniques and limits](ai_investigation_techniques.md)
- [Historical capsule retrieval and retained-only reasoning](historical_capsule_retrieval.md)
- [Implemented Operations evolution](operations_evolution_plan.md)
- [Data and artifact contracts](../DATA_SCHEMA.md)
- [Design decisions](design_decisions.md)
- [External workload boundary](external_workload_boundary.md)
- [Implementation direction and remaining changes](../FCAPSule_AI_Implementation_Proposal.md)
- [Roadmap](../ROADMAP.md) and [changelog](../CHANGELOG.md)

## Product and Evaluation Context

- [Product capabilities and value proposition](product_value_proposition.md): user-facing explanation of the current product's distinctive value and boundaries.
- [Stable concept and original research ambitions](../FCAPSule_AI_Concept.md): not a list of shipped model integrations.
- [Operator-first acceptance principles](production_product_requirements.md)
- [Current product audit](product_audit.md)
- [Investigation UX review](ux_investigation_review.md) and [visual design review](visual_design_review.md): dated implementation reviews, not perpetual test certifications.
- [Product evolution review](product_review.md): includes explicitly historical scenarios.
- [Evaluation protocol](../EVALUATION_PLAN.md), [model comparison](llm_comparison.md) and [reasoning contracts](../PROMPTS.md)
- [Historical evaluation record](evaluation_record.md): previously recorded measurements, not current production performance or a general model ranking.
- [Live integration validation](live_validation.md): one controlled Kubernetes discovery-failure run with bounded-token results and recovery checks.

For current behavior, use operating guides and source/tests first. Historical results and aspirational research requirements do not override the implemented product contract.
