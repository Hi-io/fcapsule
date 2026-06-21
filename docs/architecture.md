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
                  derived-only ZIP archive
```

Data source adapters are future boundaries. They must produce the same normalized case contract so downstream components remain unchanged.
