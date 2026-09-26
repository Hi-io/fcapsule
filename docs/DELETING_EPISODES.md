# Deleting An Episode For A Fresh Demo

Open its report and expand **Delete episode** at the bottom, beside the collapsed
engineering diagnostics. **Delete episode and shared memory** asks for explicit
confirmation and removes every incident in that episode, including local capsules,
reports, attachments and investigation revisions. Local pattern counts are derived
from retained episodes and no longer include it.

If this instance published the episode to Collective, deletion first requests
withdrawal using the original publication identity. Local records remain until
Collective confirms withdrawal. The UI waits briefly; if the service is unavailable,
finish deletion later using the same action. Publication identity and withdrawal
tombstones remain for delivery safety, without keeping the publication payload.
An unavailable, misconfigured or mismatched publisher must be corrected before
shared deletion can finish. The existing local-only DELETE API is unchanged.

Resolve the Lab fault and let the investigation finish first. An active alert can
otherwise be captured again. This operation does not erase source telemetry in
Prometheus/OpenSearch, other episodes of the same problem, exported copies, backups,
or knowledge already copied into other investigations or other instances. For a
fresh demo, remove all relevant prior episodes or use an isolated state and
Collective namespace. This is record withdrawal, not model untraining.

Automatic retention still removes local records only; it does not silently erase
long-lived shared knowledge. Archive remains reversible and is not deletion.
