# Kubernetes deployment path

Scaffolding, not a migration. Nothing here is applied. Docker Compose is
the running production setup and stays authoritative until someone
deliberately cuts over. These manifests are additive and inert: they do
not modify `compose.yaml`, the `Dockerfile`, the root `Caddyfile`,
`scripts/deploy.sh`, or the CI workflow.

Target: single-node k3s on the same EC2 t3.small that runs compose today.
Same node, same IP, same Supabase, same instance role, same DNS.

## Contents

| File | Object | Job |
|---|---|---|
| `00-namespace.yaml` | Namespace | `shortlist`, holds everything |
| `05-priorityclass.yaml` | PriorityClass | ranks the serving path above the poller for eviction |
| `10-configmap-app.yaml` | ConfigMap | non-secret env: poll interval, region, notify settings, caps |
| `11-configmap-caddy.yaml` | ConfigMap | the Caddyfile, upstream renamed to the Service |
| `20-pvc-caddy.yaml` | PersistentVolumeClaim | Caddy `/data`, the ACME account key and certificate |
| `30-deployment-web.yaml` | Deployment | stdlib API server, 1 replica |
| `31-service-web.yaml` | Service | ClusterIP `shortlist-web:8642` |
| `32-deployment-poller.yaml` | Deployment | detection loop, 1 replica, never more |
| `33-deployment-caddy.yaml` | Deployment | TLS, static bundle, `/api` proxy, hostPort 80 and 443 |
| `templates/secret.example.yaml` | Secret | TEMPLATE, placeholders only, never applied |
| `optional/cronjob-backup.yaml` | CronJob | example only, do not apply, see Backups |

`templates/` and `optional/` are subdirectories on purpose. `kubectl apply
-f k8s/` is not recursive, so it cannot reach them. Do not add `-R`.

## When this is worth doing

Be honest about the driver. The site serves a few hundred Rutgers
visitors. Compose on one node handles that with room to spare, and it will
keep handling it. Nothing about the current load calls for orchestration.

The real reason to do this is learning and portfolio signal. Kubernetes is
on most infrastructure job descriptions, and running a real workload on it
teaches things that reading cannot: scheduling under a memory ceiling,
rollout strategies, why a surge pod deadlocks on a hostPort, how ACME
state has to outlive a pod. That is a legitimate reason. It is just not a
capacity reason, and the doc should not pretend otherwise.

What this buys operationally, at this size, is small but real:

- Declarative rollouts with `rollout status` and `rollout undo`.
- Restart, probe, and eviction policy expressed as data instead of habit.
- A resource model that makes the 2 GB ceiling explicit instead of
  discovering it through an OOM kill.

What it costs is a control plane that eats roughly a quarter of the node,
one more layer to debug at 2am, and a new failure mode between the
browser and the API.

Cut over when you want to learn it, or when a second node genuinely
appears. Not because 200 visitors need it.

## Why single-node k3s and not a managed control plane

EKS charges for the control plane per hour before a single pod runs, and
that is more than the entire current bill for a workload that fits on one
small instance. EKS also wants at least two subnets and pushes toward a
managed node group, so the cheap single node stops being a single node.
GKE Autopilot and similar hosted offerings have the same shape.

k3s is a single binary, runs the whole control plane in one process, uses
sqlite instead of etcd, and installs in about thirty seconds. On a 2 GB
node that difference is decisive: a full kubeadm control plane does not
comfortably share 2 GB with a headless Chromium poller.

The tradeoff is honest. One node means the control plane and the workload
die together. There is no high availability here and there is no pretence
of it. That is the same posture compose has today, and the node is already
the single point of failure. k3s does not make that worse. It also does
not make it better, which is worth saying plainly.

## Ingress: Caddy stays, Traefik goes

k3s ships Traefik as the default ingress controller. This setup disables
it and runs Caddy as a pod instead. There is no Ingress object in this
directory, and that is the decision rather than an omission.

**Why Caddy.**

1. Traefik does not serve static files. The React bundle needs a file
   server no matter what, so the Traefik path is Traefik plus cert-manager
   plus a static-serving pod. That is three components replacing one.
2. cert-manager is three more Deployments on a node with no memory to
   spare. Caddy does ACME in-process, in the same 40 MB it already uses.
3. `short-list.app` is HSTS-preloaded. Browsers refuse plain HTTP on it,
   so there is no degraded mode and no way to debug a broken certificate
   over HTTP. The existing Caddy already holds a working certificate, and
   the cutover copies its `/data` directory into the PVC. Nothing is
   reissued and the ACME rate limit is never touched.
4. Let's Encrypt allows 5 duplicate certificates per exact name set per
   week. Two failed cert-manager attempts plus a rollback and a retry can
   spend that. Then the site is down for a week with no HTTP fallback.
   This is the risk that decides it.
5. One config file changes, and only by one line. The Caddyfile in
   `11-configmap-caddy.yaml` is the root Caddyfile with `reverse_proxy
   web:8642` changed to `reverse_proxy shortlist-web:8642`. Identical TLS,
   gzip, `/api` split, and SPA fallback. A behavioural regression has
   nowhere to hide.

**What Traefik plus cert-manager would buy.** Standard Ingress objects
that a reviewer recognises immediately, and a certificate story that
survives moving to more than one node. Caddy's ACME state lives in a
node-local volume, so a second node would need that solved. Portfolio
value slightly favours Traefik, because `kind: Ingress` is the artifact
people look for.

That is a real point and it does not win. The setup that keeps a live
HSTS-preloaded site up beats the setup that demonstrates a more common
API. Revisit it when a second node exists, because that is the point at
which node-local ACME state actually breaks.

The shape, for the record, if it is ever revisited: install cert-manager,
one `ClusterIssuer` for Let's Encrypt HTTP-01, one `Ingress` with
`spec.tls` naming a secret cert-manager fills, and a second backend
serving `/` from the bundle. Rehearse against the ACME staging endpoint
first, and remember that staging certificates are untrusted, so the
rehearsal cannot be verified in a browser on a `.app` domain.

**Why hostPort and not a LoadBalancer Service.** k3s ships klipper-lb for
`type: LoadBalancer`. Two reasons against it. It is another pod on a node
with no memory to donate. More importantly, the default
`externalTrafficPolicy: Cluster` source-NATs the client address to the
node IP. Caddy would then stamp every `X-Forwarded-For` with the same
value, and the per-IP rate limiter in `web.py` would collapse every
visitor into one bucket. hostPort preserves the client address.

## Does it fit in 2 GB

Short answer: yes, with about 30 percent of the node spare at a realistic
peak, and roughly 5 percent spare in a worst case that does not occur in
practice. It fits. The margin is thin enough to respect.

**The floor.** t3.small reports about 1987 MiB usable.

| Consumer | Memory |
|---|---|
| Ubuntu 24.04 base: kernel, systemd, sshd, journald | ~200 MiB |
| k3s server, containerd, kubelet | ~450 MiB |
| CoreDNS | ~40 MiB |
| local-path-provisioner | ~15 MiB |
| metrics-server | ~40 MiB |
| **Floor** | **~745 MiB** |

Traefik and klipper-lb are disabled at install, which is another 80 MiB
that never gets spent.

**The workloads.**

| Pod | Request | Limit | Actual steady | Actual peak |
|---|---|---|---|---|
| web | 128Mi | 256Mi | 90-120 MiB | same |
| poller | 320Mi | 768Mi | 140-160 MiB | ~500 MiB while Chromium runs |
| caddy | 48Mi | 128Mi | ~30 MiB | same |
| **Total** | **496Mi** | **1152Mi** | **~290 MiB** | **~650 MiB** |

The poller is the whole problem. Every 120 seconds the Apple detector
launches headless Chromium, drives one search, and closes it. Chromium is
not resident between cycles. So the request is sized for steady state and
the limit sits above the peak. Requesting the peak would reserve 500 MiB
that is idle most of the time, on a node that has none to reserve.

**The arithmetic.**

- Steady: 290 MiB workloads plus 745 MiB floor is about 1035 MiB, 52
  percent of the node.
- Realistic peak, Chromium running: 650 plus 745 is about 1395 MiB, 70
  percent.
- Paper worst case, every pod at its limit at once: 1152 plus 745 is about
  1897 MiB, 95 percent. This needs web to triple its resident size at the
  same moment Chromium runs. It has never done that.

**Scheduling.** This is where over-requesting bites. k3s reserves nothing
for itself by default, so the scheduler will happily hand out memory the
control plane is already using, and the first symptom is the kubelet
OOM-killing k3s. The install command below sets
`system-reserved=memory=512Mi` and a 128Mi eviction threshold, which
leaves about 1347 MiB allocatable. Requests total 496Mi of workload plus
about 140Mi of system pod requests, so 636Mi is reserved and 711 MiB is
free for bursts. The poller can burst 448 MiB above its request and web
128 MiB, which is 656 MiB and fits inside that 711 MiB.

**What compose costs by comparison.** The same 290 MiB of workloads plus
the docker daemon at about 80 MiB plus the OS at 200 MiB is roughly 570
MiB, 29 percent of the node. k3s costs about 465 MiB, a quarter of the
node, and delivers nothing the visitor can see. That is the real price and
it should be stated as such.

**Verdict: it fits, under three conditions.**

1. Every Deployment uses `strategy: Recreate`. A RollingUpdate surge on
   the poller needs a second 320Mi request that will not schedule, and a
   surge on caddy deadlocks on hostPort 80 outright.
2. `system-reserved` is set at install. Without it the scheduler
   overcommits into k3s itself.
3. Only one browser-tier detector exists. NOTES lists TikTok and Meta as
   TODO. Added sequentially in the same cycle they are fine. Two Chromium
   instances alive at once are not.

**If more margin is wanted**, in order of preference:

- t3.medium, 4 GiB, roughly double the instance cost. Buys real headroom
  and stops the conversation. This is the right answer if the browser tier
  is going to grow.
- Drop the browser tier. Remove `browser: [apple]` from
  `config/watchlist.yaml` and run the poller from the `web` image target.
  The poller request falls to 128Mi and the image falls from roughly 1.5
  GB to 250 MB, which fixes the disk pressure below as a bonus. It costs
  the Apple detector, which is the only source for Apple's umbrella
  postings.
- `--disable metrics-server` frees about 40 MiB and a 70Mi request, at the
  cost of `kubectl top`. On a node this tight, seeing memory is worth
  keeping.

**Disk, which is the other ceiling.** The poller image is python-slim plus
Playwright plus Chromium, roughly 1.5 GB. Docker and containerd keep
separate image stores, so every build holds about 3 GB across both until
pruned. Check `df -h` before starting. `deploy.sh` already runs `docker
image prune`; containerd needs `sudo k3s crictl rmi --prune` separately.
Keep two or three image tags so `rollout undo` has something to land on,
and no more.

## Before installing: instance role from a pod

SES authenticates through the EC2 instance role over IMDSv2. No AWS key
exists anywhere, and that must stay true.

Pods reach the metadata service at 169.254.169.254 through the CNI, which
adds a network hop, the same way the docker bridge does. If the instance
metadata hop limit is 1, the role lookup fails inside pods and SES stops
working with a confusing credential error.

Check it, and set it to 2 if needed:

```bash
aws ec2 describe-instances --instance-ids i-0bdc94e038834bec6 \
  --query 'Reservations[].Instances[].MetadataOptions'

aws ec2 modify-instance-metadata-options --instance-id i-0bdc94e038834bec6 \
  --http-tokens required --http-put-response-hop-limit 2
```

This requirement applies to the compose containers today, not just to k3s.
The email channel is still dark pending SES sandbox exit and
`NOTIFY_POSTAL_ADDRESS`, so it has never been exercised either way. Check
it before blaming Kubernetes for it.

## Install k3s

```bash
curl -sfL https://get.k3s.io | INSTALL_K3S_CHANNEL=stable sh -s - \
  --disable traefik \
  --disable servicelb \
  --write-kubeconfig-mode 600 \
  --kubelet-arg=system-reserved=cpu=200m,memory=512Mi \
  --kubelet-arg='eviction-hard=memory.available<128Mi'
```

Quote the eviction argument. The `<` is a shell redirect otherwise, and
the install silently proceeds with the default threshold.

Pin an exact version with `INSTALL_K3S_VERSION=vX.Y.Z+k3s1` once you know
which one you tested against, so a reinstall six months later does not
land on a different minor.

The install does not touch ports 80 or 443, because Traefik is disabled.
Compose keeps serving throughout.

Verify:

```bash
sudo k3s kubectl get nodes
sudo k3s kubectl -n kube-system get pods
sudo k3s kubectl describe node | grep -A6 Allocatable
```

The kubeconfig stays at mode 600. Every command below uses `sudo k3s
kubectl`, which reads it as root. `--write-kubeconfig-mode 644` is more
convenient and hands cluster-admin to any local user, which on a
single-user box is a defensible trade but should be a decision, not an
accident.

Uninstall, which is part of the rollback path:
`/usr/local/bin/k3s-uninstall.sh`.

## Images

Built on the box with docker, imported into containerd with `ctr`. No
registry.

```bash
cd /opt/ruemployed
SHA=$(git rev-parse --short HEAD)
sudo docker build --target web      -t shortlist-web:$SHA .
sudo docker build --target poller   -t shortlist-poller:$SHA .
sudo docker build --target frontend -t shortlist-frontend:$SHA .
for i in web poller frontend; do
  sudo docker save shortlist-$i:$SHA | sudo k3s ctr images import -
done
```

**Why ctr and not a registry.** The build already happens on the box;
`deploy.sh` does exactly that today. A registry adds an account, a pull
secret, and a 1.5 GB pull over the network on every poller change, to
solve a distribution problem that does not exist with one node.

**What it costs.** It does not scale past one node, and `docker save`
piped into `ctr import` for the poller image takes tens of seconds and
holds the image in both stores at once. When a second node appears, or
when that wait becomes annoying, move to GHCR: build in the CI job that
already exists, push, and let the node pull. That is the upgrade path, not
today's problem.

**Two rules that follow from ctr.** Images must be pulled never, so
`imagePullPolicy: IfNotPresent` is set explicitly on every container.
Never tag an image `:latest`, because that flips the default policy to
`Always` and every rollout then fails looking for a registry.

The manifests carry `:v0` as a bootstrap tag. Build the three images once
as `v0` for the first apply, or apply and immediately `set image` to the
sha. After that every deploy uses a git sha, which also makes `rollout
undo` land somewhere meaningful.

## Create the Secret

Never from the template file. Create it on the box from the `.env` that
already holds the values:

```bash
cd /opt/ruemployed
sudo k3s kubectl -n shortlist create secret generic shortlist-secrets \
  --from-literal=DATABASE_URL="$(sed -n 's/^SUPABASE_URL_POSTGRES=//p' .env)" \
  --from-literal=ANTHROPIC_API_KEY="$(sed -n 's/^ANTHROPIC_API_KEY=//p' .env)" \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Note the rename. Compose maps the `.env` key `SUPABASE_URL_POSTGRES` onto
the container variable `DATABASE_URL`, which is what `store.py` reads. The
Secret key must be `DATABASE_URL`.

`--dry-run=client` piped into `apply` makes the line idempotent, so the
same command creates the Secret the first time and rotates it after that.

`ANTHROPIC_API_KEY` is optional. Both the verifier and the company
resolver stay off without it, which is production's current state. The
poller mounts it with `optional: true` so the pod starts either way.

**AWS credentials are deliberately absent.** There are none to add. boto3
resolves the EC2 instance role over IMDSv2, so SES authenticates with no
long-lived key in `.env`, in an image, or in this cluster. Adding an
`AWS_SECRET_ACCESS_KEY` here would be a downgrade.

**Secrets are base64, not encryption.** Anyone with cluster access can
read them, and k3s stores them unencrypted in its sqlite datastore under
`/var/lib/rancher/k3s/server/db` unless secrets encryption is enabled at
install. On a single-node box that is the same trust boundary as a mode
600 `.env` file. No better, no worse. Do not describe it as encrypted.

## Apply

```bash
sudo k3s kubectl apply -f k8s/
```

The numeric filename prefixes are the apply order, and kubectl applies a
directory in filename order. Namespace, then priority class, then config,
then the PVC, then workloads.

`-f` on a directory is not recursive, so `templates/secret.example.yaml`
and `optional/cronjob-backup.yaml` are skipped. That is why they live in
subdirectories. Never run this with `-R`: it would overwrite the real
Secret with placeholders, and nothing would break until the next pod
restart, which is the worst possible time to find out.

There is no `kustomization.yaml`. One environment, one node, no overlays
to compose. Filename ordering does the same job with one less tool.

## Verify a rollout

```bash
sudo k3s kubectl -n shortlist rollout status deployment/shortlist-web --timeout=120s
sudo k3s kubectl -n shortlist rollout status deployment/shortlist-caddy --timeout=120s
sudo k3s kubectl -n shortlist get pods -o wide
sudo k3s kubectl -n shortlist top pods
```

Then check the things Kubernetes cannot tell you:

```bash
# the API answers through the real origin, with a valid certificate
curl -sS -o /dev/null -w '%{http_code}\n' https://short-list.app/api/postings
# the React bundle is served, not the stdlib fallback page
curl -sS https://short-list.app/ | head -c 200
# the poller is running cycles, not crash-looping quietly
sudo k3s kubectl -n shortlist logs deploy/shortlist-poller --tail=50
# the certificate is on the PVC, so a restart will not reissue
sudo k3s kubectl -n shortlist exec deploy/shortlist-caddy -- \
  find /data/caddy/certificates -name '*.crt'
```

A pod that is `Running` proves the process started. It does not prove the
site works. Check the origin.

Signals that something is wrong: `Pending` with `Insufficient memory`
means a request is too large or `system-reserved` is unset. `OOMKilled` on
the poller means Chromium exceeded 768Mi. `CrashLoopBackOff` on caddy with
a bind error means compose still holds port 80.

## Roll back

Three levels, cheapest first.

**A bad image.** The previous sha is still in containerd.

```bash
sudo k3s kubectl -n shortlist rollout undo deployment/shortlist-web
sudo k3s kubectl -n shortlist rollout status deployment/shortlist-web
```

**A bad manifest.** Fix the file and apply again. `revisionHistoryLimit:
3` keeps three revisions to undo through.

**The whole experiment.** Compose is untouched and its volumes still hold
the SQLite fallback and a working certificate.

```bash
sudo k3s kubectl -n shortlist scale deployment --all --replicas=0   # frees 80 and 443
cd /opt/ruemployed && sudo docker compose up -d
```

Downtime is a few seconds. Optionally `/usr/local/bin/k3s-uninstall.sh`
afterwards to reclaim the memory.

Do not delete any docker volume and do not uninstall docker while
evaluating k3s. `caddy_data` holds the certificate that makes the fast
rollback fast, and `data` holds the frozen SQLite fallback.

## Cut over from compose

Same node, same IP. DNS does not change and no propagation wait exists.
Total downtime is a few seconds if the images are imported first.

```bash
cd /opt/ruemployed

# 1. Stop duplicate detection first. Two pollers double the ATS request
#    rate and the Claude spend. The site stays up: caddy and web are
#    still running under compose.
sudo docker compose stop poller

# 2. Images and Secret, per the sections above.

# 3. Everything except the ingress.
sudo k3s kubectl apply -f k8s/
sudo k3s kubectl -n shortlist rollout status deployment/shortlist-web

# 4. The caddy pod is now crash-looping, because compose still holds
#    ports 80 and 443. That is expected and it is useful: scheduling the
#    pod is what makes local-path provision the PVC directory, which does
#    not exist until a pod that mounts it is scheduled.
sudo k3s kubectl -n shortlist scale deployment/shortlist-caddy --replicas=0

# 5. Copy the existing certificate into the PVC, so nothing is reissued
#    and the Let's Encrypt rate limit is never touched. Confirm both
#    paths first: the compose volume name derives from the directory
#    name, and the PVC directory name contains a generated uid.
sudo docker volume ls | grep caddy_data
sudo ls /var/lib/rancher/k3s/storage/
sudo cp -a /var/lib/docker/volumes/ruemployed_caddy_data/_data/. \
           /var/lib/rancher/k3s/storage/pvc-<uid>_shortlist_caddy-data/

# 6. The swap. This is the only downtime.
sudo docker compose down
sudo k3s kubectl -n shortlist scale deployment/shortlist-caddy --replicas=1
sudo k3s kubectl -n shortlist rollout status deployment/shortlist-caddy

# 7. Verify against the real origin, per the section above.
curl -sS -o /dev/null -w '%{http_code}\n' https://short-list.app/api/postings
```

If step 5 is skipped, Caddy requests a fresh certificate at step 6 and the
site is down for the 10 to 30 seconds that HTTP-01 takes, with two of the
five weekly duplicate certificates spent. Survivable once. Not survivable
as a habit.

**One behavioural difference to know about.** These manifests are
Postgres-only. There is no `/data` volume and no SQLite fallback. Under
compose, clearing `DATABASE_URL` reverts to the SQLite file on the shared
volume, which NOTES documents as the frozen fallback. Under k8s, clearing
it would give web and poller each their own empty ephemeral SQLite inside
their own container, and they would not share it. That is deliberate,
because production has been Postgres since the Supabase cutover, but it
means the k8s path has no SQLite escape hatch. The escape hatch is the
compose rollback above.

## What changes in CI, and what does not

**Changes: one file, `scripts/deploy.sh`.** Not modified here. The
replacement body would be:

```sh
#!/bin/sh -e
cd /opt/ruemployed
git pull --ff-only
SHA=$(git rev-parse --short HEAD)
for t in web poller frontend; do
  sudo docker build --target $t -t shortlist-$t:$SHA .
  sudo docker save shortlist-$t:$SHA | sudo k3s ctr images import -
done
sudo k3s kubectl apply -f k8s/
sudo k3s kubectl -n shortlist set image deployment/shortlist-web    web=shortlist-web:$SHA
sudo k3s kubectl -n shortlist set image deployment/shortlist-poller poller=shortlist-poller:$SHA
sudo k3s kubectl -n shortlist set image deployment/shortlist-caddy  frontend=shortlist-frontend:$SHA
sudo k3s kubectl -n shortlist rollout status deployment/shortlist-web --timeout=180s
sudo docker image prune -f >/dev/null
echo "deployed $SHA"
```

`docker compose build` and `docker compose up -d` become `apply` plus
`set image` plus `rollout status`. The deploy now waits for the rollout
and fails loudly if it does not converge, which compose never did.

Order matters in that script. `apply` resets the image tag to the `:v0` in
the manifests, so the `set image` lines must follow it, never precede it.
That is the one sharp edge of keeping tags in git and setting them
imperatively. Running them in this order makes it a non-issue.

The last `set image` targets an initContainer. `kubectl set image` matches
init containers by name, but if a kubectl version ever disagrees, the
explicit form is:

```sh
sudo k3s kubectl -n shortlist patch deployment shortlist-caddy --type=json \
  -p '[{"op":"replace","path":"/spec/template/spec/initContainers/0/image","value":"shortlist-frontend:'$SHA'"}]'
```

**Unchanged, and this is most of it.**

- `.github/workflows/deploy.yml`. It runs `ssh ubuntu@... deploy`, and the
  forced-command key still resolves that to `scripts/deploy.sh`. The
  workflow does not know or care what the script does. The pinned host
  key, the concurrency group, and the test and frontend gates are all
  untouched.
- Image build. Same `Dockerfile`, same three targets, same layers.
- Supabase. Same database, same connection string, same RLS policy. There
  is no state on the node, so there is nothing to migrate.
- The EC2 instance role. Same role, same IMDSv2 path, still no AWS key
  anywhere.
- DNS. Same A record to the same IP on the same node.
- The security group. Still 80 and 443 open, 22 restricted.
- `.github/workflows/backup.yml`. Stays in Actions. See below.

**Rollback for CI specifically.** Restore the previous `deploy.sh` and
push. The forced-command key runs whatever is in the file at that path, so
reverting the script reverts the deploy mechanism with no server-side
change.

## Backups

The nightly backup stays in `.github/workflows/backup.yml`. It does not
map cleanly to a CronJob, and `optional/cronjob-backup.yaml` exists only
to show the shape. Do not apply it.

The reasoning. The database is Supabase, not the node, so nothing about
this job gets easier by moving closer to the cluster. There is no local
database that Actions cannot already reach. Meanwhile the dump would land
on the node's EBS root volume, and the node is the single point of failure
in this whole system: putting the backup on the machine most likely to die
is the one place a backup must not be. `BACKUP_PASSPHRASE` would move onto
the box, where a passphrase stored next to its own ciphertext protects
nothing. Retention is `retention-days: 90` in Actions and a `find -mtime`
cron job here, on a disk that already holds multi-GB poller images. And no
image carries both pg_dump 17 and gpg, so the job would install gnupg from
a package mirror on every run, which adds a failure mode to the one thing
that must not fail.

The one variant that would beat Actions: dump, encrypt, then `aws s3 cp`
to a bucket, authenticating with the instance role that is already
attached, with retention as an S3 lifecycle rule. That is genuinely
offsite and needs no new secret. It also needs a bucket and an IAM policy
addition, neither of which exists. Worth doing on its own merits, not as
part of a k3s move.

## Left out on purpose

- **HorizontalPodAutoscaler.** Nothing here scales. The poller must not,
  for the reasons in its manifest, and web at a few hundred visitors a day
  is not close to needing a second replica.
- **Ingress object and cert-manager.** Covered above. This is the
  decision, not an oversight.
- **NetworkPolicy.** k3s does enforce these, so it would be real rather
  than decorative. Three pods in one namespace with a single hostPort
  entry point makes it low value on day one. It is cheap to add later and
  a reasonable next exercise.
- **Helm chart or kustomize overlays.** Both parameterize across
  environments. There is one environment.
- **ResourceQuota and LimitRange.** One namespace, one owner, and every
  container already carries explicit requests and limits.
- **PodDisruptionBudget.** One node. A drain takes everything down whether
  a budget exists or not.
- **ServiceAccount and RBAC.** No pod calls the API server, so the default
  service account with no permissions is correct. Every pod sets
  `automountServiceAccountToken: false` so the token is not mounted at
  all.
- **Prometheus, Grafana, or any logging stack.** They would cost more
  memory than everything they observe. `kubectl top`, `kubectl logs`, and
  the existing Supabase row-freshness signal are the ceiling on this node.
- **A liveness probe on the poller.** It serves no port and exposes no
  health endpoint, so an HTTP probe has nothing to hit and an exec probe
  would only prove the process table is non-empty. A wedged cycle is
  caught by watching row freshness, not by the kubelet. This is a real
  gap. It is the same gap compose has today.
- **`runAsNonRoot`.** The images have no non-root user. Adding one is a
  Dockerfile change, and the Dockerfile is out of scope for this
  directory. Real, unfixed debt, worth doing when the Dockerfile is next
  touched.
- **Multi-node anything.** Node affinity, topology spread, and replicated
  storage all describe a cluster that does not exist.
