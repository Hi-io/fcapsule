# P1 Architecture

```text
metadata.yaml  alert.json  prometheus_metrics.json  opensearch_logs.json
       \          |                 |                       /
                    validated CaseBundle
                            |
             +--------------+---------------+
             |              |               |
       entity resolver  log reducer  metrics analyzer
             |              |               |
             +---------- alert timeline ----+
                            |
                 evidence scorer/selector
                            |
                grounded hypothesis generator
                            |
                     evidence verifier
                            |
             capsule + baselines + evaluation
                            |
              optional DeepSeek model comparison
                            |
                    static review dashboard
                            |
                  derived-only ZIP archive
```

Data source adapters are future boundaries. They must produce the same normalized case contract so downstream components remain unchanged.

## Domain Flow

P1 treats domains as operational telemetry signal families:

- `fault_events`: alerts and incident event streams;
- `log_text`: semi-structured application logs;
- `time_series_metrics`: numeric samples over time;
- `topology_metadata`: service, namespace, pod, cluster, and CNCC identity context;
- `llm_reasoning`: optional generated interpretation over the selected evidence.

The dashboard renders these domains separately so reviewers can see whether the capsule preserved evidence across the different signal families.
