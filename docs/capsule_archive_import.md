# Import a Retained Capsule Archive

Import a verified FCAPSule archive into the local control-plane store:

```bash
python -m fcapsule.cli import-archive \
  --archive /path/to/fcapsule_incident-123.zip \
  --state-dir ./.fcapsule
```

The command restores the archive under `STATE_DIR/imported-capsules/`, registers a new local application, incident, episode and capsule, and prints their local IDs. Open the local console against the same state directory to review the retained report and evidence. Re-importing creates a separate local record.

Only supported FCAPSule archives with a valid integrity manifest, `capsule.json`, `evidence.json`, and a version `1.3` `incident_report.json` are accepted. File hashes and archive paths are checked during extraction, and expanded content is limited to 512 MiB. These checks detect corruption and unsafe archive paths; they do **not** authenticate who created an archive or make its contents trustworthy. Import only archives from a source you trust, and treat retained examples as potentially sensitive.

Archives created before the integrity manifest was introduced are rejected by `import-archive`. If you choose to trust a legacy ZIP's contents despite its unverifiable origin, repack it to a new file first:

```bash
python -m fcapsule.cli repack-legacy-archive \
  --archive /path/to/legacy.zip \
  --out /path/to/repacked.zip \
  --accept-unverified-origin

python -m fcapsule.cli import-archive \
  --archive /path/to/repacked.zip \
  --state-dir ./.fcapsule
```

The repacker requires that explicit acknowledgment, leaves the original ZIP untouched, and accepts only flat allowlisted capsule files. It rejects duplicate entries, traversal paths, symlinks, encrypted entries and archives over 512 MiB; it also requires the retained capsule/evidence files and a version `1.3` report. The potentially executable `dashboard.html` is omitted. The new manifest hashes the extracted bytes **from repack time onward**; it cannot attest the legacy ZIP's original integrity or provenance. Do not use this command for an archive whose contents you do not trust. Repacking and importing are separate local steps; the repacker does not register records or publish to Collective.

A smoke test with a 134,158-byte pre-manifest FCAPSule archive successfully repacked it and then passed strict import. The source archive was copied to an ignored local test directory and was not modified; this verifies format compatibility, not source authenticity. Do not add a manifest by hand or bypass `import-archive` verification.

The imported record is source-read-only: it keeps the captured report and evidence references, but the original telemetry directory is not restored. Building or rebuilding a report from source data is unavailable. The configured local incident-retention period starts at import time, not at the historical incident time. Imported records are excluded from automatic Collective publication, including background rescans.

This is a local CLI workflow; there is no archive-import API. The archive is not a full telemetry backup: source telemetry and raw attachment blobs are not restored unless they are explicitly present as retained archive files.
