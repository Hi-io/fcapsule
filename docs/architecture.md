# Endterm Architecture

FCAPSule combines two layers: a local investigator that collects and interprets incident evidence, and optional shared knowledge supplied by **Collective**. Source adapters produce a common incident contract, so evidence processing is separate from the source-specific queries.

## Evidence Flow

```text
Prometheus       OpenSearch       Kubernetes       Operator evidence
 alerts/metrics     logs        workload/config    text/image/audio
       |             |                |                  |
       +-------------+----------------+          model extraction
                     |                                   |
             bounded source capture                      |
                     |                                   |
       log reduction + metric analysis + entity alignment |
                     |                                   |
          domain-balanced evidence selection             |
                     |                                   |
              retained capsule --------------------------+
                     |
             episode investigator <---- local history
                |            ^
          request check      | observation
                +---- scoped read-only tools
                     |
          cited assessment + next action
                     |
           Operations / API / export
                     |
        publish / retrieve selected knowledge
                     |
              Collective API ---> PostgreSQL
```

## Identity, Alerts and Episodes

Live discovery aligns evidence by cluster, namespace and affected workload. An explicit pod identifier takes precedence. Configurable mappings from alert labels to pod labels also support component-level identity, including CNFC identifiers used in telecommunications. A component can contain several replicas; capture records the matched scope and any omitted pods.

Repeated delivery of one firing alert is idempotent. Related signals within the same application's 15-minute activity window form an episode while retaining separate source reports. Later occurrences remain distinct episodes and can appear in **Patterns**. These groupings organize investigation work without asserting that every grouped signal has the same cause.

The normalized incident directory is the adapter boundary. Prometheus, OpenSearch and Kubernetes are the supplied live implementations; imported cases use the same downstream pipeline. See [data contracts](../DATA_SCHEMA.md).

## Evidence Processing

Log processing masks variable fields and groups repeated messages into representative templates while preserving diagnostic distinctions. Numerical processing compares series with a reference window, computes robust deviations and retains bounded trends, units and important peaks. This time-series stage is deterministic statistical analysis, not another pretrained model.

The evidence selector combines severity, anomaly magnitude, time proximity, entity match, rarity, relevance and repetition penalties. Per-domain capacity prevents a large family of correlated metrics from displacing logs or alert evidence. Selected observations retain source, time, scope and evidence identifiers.

The capsule and deterministic report are saved before model investigation. This separates successful evidence capture from provider availability and makes the retained record useful independently of any generated explanation.

## Investigation Loop

1. Build a compact context from the episode's retained evidence and preserve mutable workload state.
2. Add eligible local history and, when enabled, relevant Collective observations as historical leads.
3. Ask the configured language model for a scoped check or an assessment.
4. Validate the request against the allowed tools, resource identity and remaining budget. Run accepted checks and return their observations to the model.
5. Stop when the model has sufficient evidence or the bounded allowance is reached. Review the assessment against visible evidence and validate its structured references.
6. Persist the assessment, next action, competing explanations, checks, provenance and usage. Additional operator evidence can produce a new revision.

Tools inspect workload/configuration state, relevant logs and metrics, monitoring discovery, declared dependencies and retained cases. The model does not receive arbitrary shell access or unrestricted source queries. Repeated identical checks are rejected. Telemetry and uploaded content are treated as evidence, not as executable instructions.

The service defaults to a 3,200-token estimated input cap per complete request, a 12,000-token total reserve and one optional model-selected check, configurable up to four. Required observations are recorded separately from that optional count. Context compaction prioritizes identity, critical current evidence, completed observations and explicitly added evidence. A final assessment omits unused tool descriptions. Provider usage and conservative reservations are both retained; the budgets are token controls rather than a fixed currency charge.

Each provider call records the evidence identifiers it could see. Citation validation rejects unavailable references. Model-assisted review checks the proposed explanation against the observations; engineers can inspect both the result and its basis rather than relying on a score alone.

## Multimodal Evidence

The language model handles investigation; a visual specialist extracts screenshot observations; a speech specialist transcribes operator audio. Settings configures and validates these capabilities independently. Specialist output enters the same evidence context, with provider/model identity and observation provenance. Explicit review produces a new assessment without overwriting the earlier one. Metric summaries are part of this context, alongside the three pretrained-model roles.

## Local and Shared Knowledge

Local recurrence lookup selects a bounded set of earlier retained capsules using application, resource and alert identity. The investigator examines observations from a relevant prior episode and compares them with the current incident. Earlier model explanations remain historical hypotheses.

Collective supports knowledge transfer across independent instances through minimized, versioned cases. FCAPSule publishes selected observations and retrieves candidate cases; the requesting instance decides which current checks those leads justify. Collective runs no LLMs and holds no provider keys, keeping shared storage/retrieval separate from per-instance inference costs. An unavailable Collective endpoint does not block local capture or investigation. See [Collective](collective.md).

## Persistence and Lifecycle

SQLite stores local metadata and references; the state directory stores captures, capsules, attachments and investigation records. Source captures and retained artifacts have distinct retention policies. Current reports open from retained data without requiring the original telemetry source.

Background workers coalesce updates for one episode and use input fingerprints to avoid rerunning an unchanged completed investigation. Interrupted unarchived work can resume on startup. Removing an episode member invalidates shared derived analysis so its evidence is not retained in surviving copies. The [operations guide](operations.md) distinguishes archiving, local retention and explicit shared-memory withdrawal.

## Implementation Map

| Location | Responsibility |
| --- | --- |
| `fcapsule/adapters/`, `fcapsule/live_sources.py` | Source queries, identity resolution, discovery and capture |
| `fcapsule/processing/`, `fcapsule/attention/` | Log/metric analysis and evidence selection |
| `fcapsule/io/`, `fcapsule/incident_report.py` | Input contracts, retained reports and portable archives |
| `fcapsule/episode_investigation.py` | Agent decisions, budgets, compaction and assessment validation |
| `fcapsule/investigation_tools.py` | Allowed checks and evidence context |
| `fcapsule/investigation_service.py`, `fcapsule/evidence_service.py` | Investigation scheduling, revisions and uploaded evidence |
| `fcapsule/estima_client.py`, `fcapsule/estima_publisher.py` | Collective client, minimized projection and publication outbox |
| `fcapsule/store.py`, `fcapsule/control_plane.py` | Local persistence and lifecycle coordination |
| `fcapsule/ui/`, `fcapsule/cli.py` | Operator interface, API and command-line workflows |
| `fcapsule/evaluation/`, `tests/` | Evidence metrics, comparison baselines and automated checks |

Endterm uses one FCAPSule process with background workers and persistent local storage. Collective has its own service/database boundary. The separate Lab owns synthetic workloads and controlled faults; it is not embedded in either runtime.
