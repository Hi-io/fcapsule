# HTTPS and Microphone Access

Browser recording requires a secure context: trusted HTTPS or localhost. A plain
HTTP node IP does not qualify, even when the audio model is validated. Uploading
an existing audio file remains possible without recording permission.

## Local Access Without Certificates

```bash
kubectl -n fcapsule port-forward service/fcapsule 8765:8765
```

Keep the terminal open and visit `http://localhost:8765/console`. Allow microphone
access when the browser asks. A validated audio model is still required to
transcribe a recording. This does not make the node IP a secure origin.

## Optional HTTPS for a Private Network

The optional Caddy sidecar terminates TLS on port 8443 and forwards requests to
FCAPSule on localhost. It does not change HTTP access used by existing integrations.
It adds a NodePort on 30767; adapt that port to your deployment if it is occupied.

1. Obtain a certificate with the host name or node IP in its subject alternative
   names. For a private development cluster, a local CA such as mkcert is suitable.
   Trust its **public** root certificate only on clients you control. Keep the CA
   private key outside the repository and never upload it to the cluster.
2. Create the server TLS Secret from files outside the repository:

```bash
kubectl -n fcapsule create secret tls fcapsule-tls \
  --cert=/private/path/server.crt --key=/private/path/server.key \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f deploy/kubernetes/https-proxy.yaml
kubectl -n fcapsule patch deployment/fcapsule --type=strategic \
  --patch-file deploy/kubernetes/https-proxy-patch.yaml
kubectl -n fcapsule rollout status deployment/fcapsule
```

3. Open `https://<certificate-host-or-ip>:30767/console`. There must be no
   certificate warning. The browser still asks for normal microphone permission;
   HTTPS cannot grant that permission automatically.

The patch retains the existing application container, init containers, volumes,
and node placement. Apply it after the base deployment and development overlay.
The base pod's `fsGroup: 1000` provides read access to the group-readable TLS key.
The official Caddy executable carries a network-bind file capability, so the
sidecar permits only `NET_BIND_SERVICE` while dropping all other capabilities.
For certificate rotation, update the Secret and restart the deployment at a safe
time, after active investigations finish. Monitor certificate expiry yourself;
this static-certificate example does not provide automatic renewal.

Do not use browser flags that treat arbitrary insecure origins as secure, disable
certificate validation, or share a CA private key to make recording work. TLS is
not authentication: FCAPSule still requires a trusted network or an authenticating
reverse proxy before broader exposure. A production ingress with managed
certificates is preferable when that infrastructure is already available.

To remove the optional proxy, remove its container and three `https-*` volumes
from the deployment, then delete only `service/fcapsule-https`,
`configmap/fcapsule-https`, and `secret/fcapsule-tls`. Keep the main service and
state volume intact. Remove a locally installed root from the client trust store
when it is no longer needed.
