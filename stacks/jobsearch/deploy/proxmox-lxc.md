# Deploying the jobsearch stack on Proxmox (unprivileged LXC + Docker)

Target: a 2 GB / 2 core / 16 GB LXC on the internal network, behind your
trusted LAN segment. No public exposure at any point.

## 1. Create the LXC

From the Proxmox host (adjust VMID, storage, bridge, IP to your setup):

```bash
pct create 315 local:vztmpl/debian-12-standard_amd64.tar.zst \
  --hostname jobsearch \
  --unprivileged 1 \
  --cores 2 --memory 2048 \
  --rootfs local-lvm:16 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.30.10/24,gw=192.168.30.1 \
  --features nesting=1 \
  --start 1
```

> `--features nesting=1` is **mandatory** for Docker. Without it, containerd
> fails with confusing overlay/shim errors.

If your template is missing: `pveam update && pveam download local debian-12-standard_amd64.tar.zst`

## 2. Docker-in-LXC gotchas (read before troubleshooting)

- **Keyring errors** (`failed to create shim task`, `keyctl` errors): newer
  Docker on unprivileged LXC also needs keyctl:
  `pct set 315 -features nesting=1,keyctl=1` (then reboot the container once).
- **Overlayfs on your storage backend**: if `docker run hello-world` still
  fails, fall back to the vfs storage driver — slower, but always works:

  ```json
  // /etc/docker/daemon.json
  { "storage-driver": "vfs" }
  ```

- **Unprivileged is the point**: root inside the container maps to a high UID
  on the host. That isolation between the container's Docker and the host
  kernel is a feature — don't "fix" it with `--privileged`.

## 3. Install Docker (official apt repository — no curl-pipe-shell)

Review the steps before running them; this matches the repo convention of
inspecting installers instead of piping them blindly.

```bash
# as root inside the LXC
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# verify
docker run --rm hello-world
```

## 4. Deploy the kit

```bash
# as a normal user inside the LXC (add yourself to the docker group first)
sudo usermod -aG docker $USER && newgrp docker

git clone https://github.com/arsallls/claude-network-skills.git
cd claude-network-skills/stacks/jobsearch

cp .env.example .env
openssl rand -hex 32   # -> JOBSEARCH_API_TOKEN
openssl rand -hex 32   # -> SEARXNG_SECRET
nano .env              # paste both, review the rest
chmod 600 .env
```

Before starting: check the pinned `searxng/searxng:` tag in
`docker-compose.yml` against
[hub.docker.com/r/searxng/searxng/tags](https://hub.docker.com/r/searxng/searxng/tags)
and bump it if a newer date-hash tag exists — upgrades should be deliberate.

```bash
docker compose up -d --build
# The gateway runs as non-root (uid 1000) — give it write access to ./data,
# otherwise SQLite fails on the first persisted search:
mkdir -p data && sudo chown 1000:1000 data
docker compose ps        # both services should be "healthy"
```

## 5. Verify

```bash
./deploy/verify.sh http://192.168.30.10:8080 <JOBSEARCH_API_TOKEN>
```

Run it once from inside the LXC, then from another LAN machine. Acceptance =
all 8 checks pass plus one successful agent query from a second machine
(see the kit README "Testing with agents" section).

## 6. Ops

- **Backups**: `pct snapshot 315 jobsearch-$(date +%F)` covers the whole
  stack including the SQLite data in `./data/`.
- **Upgrades**: bump the pinned SearXNG tag (and gateway pins on any code
  change), then `docker compose pull && docker compose up -d --build`.
- **Logs**: `docker compose logs -f jobsearch-gateway`.
- **Firewall posture**: the gateway publishes only on the LXC's internal
  IP. Keep it that way — this service should never be reachable from the
  internet.