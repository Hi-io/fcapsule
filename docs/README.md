# FCAPSule Documentation

Start with the [product overview](../README.md) or [Observability With Memory](product_value_proposition.md). The first explains what is running today; the second explains why the AI investigator, retained capsules and recurrence memory matter.

## Operate

| Need | Guide |
| --- | --- |
| Use the incident queue, reports, Patterns, Targets and Settings | [Operations](operations.md) |
| Install and iterate on the single-replica Kubernetes service | [Kubernetes deployment](kubernetes_deployment.md) |
| Configure providers and understand model-call limits | [LLM provider operations](llm_provider_operations.md) |
| Use a microphone from a trusted browser context | [HTTPS and microphone access](https_access.md) |
| Understand capture storage, masking, credentials and deletion | [Privacy and retention](data_privacy.md) |

The [separate FCAPSule Lab](https://github.com/Hi-io/fcapsule-lab) generates test workloads. It is not installed or required by FCAPSule.

## Understand the System

- [Architecture](architecture.md) and [engineering design](../PROJECT_DESIGN.md): runtime components, source ownership and deployment shape.
- [AI investigation techniques](ai_investigation_techniques.md): the actual read-only tools, evidence selection, model calls, grounding checks and limitations.
- [Historical capsule retrieval](historical_capsule_retrieval.md): how retained evidence can inform a later incident without treating an old model answer as fact.
- [Optional image and audio evidence](multimodal_evidence.md): capability gates, provenance and reviewed reassessment.
- [Data and artifact schema](../DATA_SCHEMA.md), [design decisions](design_decisions.md) and [external workload boundary](external_workload_boundary.md).
- [Current product guide](../FCAPSule_AI_Project_Guide.md) and [roadmap](../ROADMAP.md).

## Research and History

The [concept](../FCAPSule_AI_Concept.md) describes the durable problem and academic framing. The [implementation proposal](../FCAPSule_AI_Implementation_Proposal.md) separates shipped choices from future hardening. The [evaluation plan](../EVALUATION_PLAN.md), [model comparison protocol](llm_comparison.md) and [prompt contract](../PROMPTS.md) explain how to test the approach without presenting benchmark scores as operator facts.

Dated design reviews, early experiments and validation snapshots live in the [history archive](history/README.md). They document how the product changed; they are not instructions for the current UI or guarantees about current cluster performance.

**Documentation rule:** a live capability is described by the operating guides and code/tests. A proposed capability belongs in the roadmap. A dated result stays labeled with its source revision and conditions.
