# Import a Retained Capsule Archive

Import a verified FCAPSule archive into the local control-plane store:

```bash
python -m fcapsule.cli import-archive \
  --archive /path/to/fcapsule_incident-123.zip \
  --state-dir ./.fcapsule
```

The command restores the archive under `STATE_DIR/imported-capsules/`, registers a new local application, incident, episode and capsule, and prints their local IDs. Open the local console against the same state directory to review the retained report and evidence. Re-importing creates a separate local record.

Only supported FCAPSule archives with a valid integrity manifest, `capsule.json`, `evidence.json`, and a version `1.3` `incident_report.json` are accepted. File hashes and archive paths are checked during extraction, and expanded content is limited to 512 MiB. These checks detect corruption and unsafe archive paths; they do **not** authenticate who created an archive or make its contents trustworthy. Import only archives from a source you trust, and treat retained examples as potentially sensitive.

Archives created before the integrity manifest was introduced are rejected as unverified. There is no supported legacy-ZIP upgrade command. If the trusted original case or capsule output is still available, generate a fresh archive with a current FCAPSule runtime; if the old ZIP is the only copy, keep it outside the imported record workflow. Do not add a manifest by hand: hashing an old ZIP after receipt would not verify its original integrity or provenance. Any future legacy conversion should be a separate, explicit operator-approved repack, not a fallback in this importer.

The imported record is source-read-only: it keeps the captured report and evidence references, but the original telemetry directory is not restored. Building or rebuilding a report from source data is unavailable. The configured local incident-retention period starts at import time, not at the historical incident time. Imported records are excluded from automatic Collective publication, including background rescans.

This is a local CLI workflow; there is no archive-import API. The archive is not a full telemetry backup: source telemetry and raw attachment blobs are not restored unless they are explicitly present as retained archive files.
