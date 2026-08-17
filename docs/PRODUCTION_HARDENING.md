# Production hardening — before you expose LEco on a real domain

> **Open source** · [MIT License](../LICENSE) · Maintained by [Techtonic Systems Media And Research LLC](https://techtonic.systems/)

LEco DevOps was designed for a laptop, where every default assumes a trusted single-user machine on `127.0.0.1`. Those same defaults are dangerous on a server with a public IP.

**This is not a list of nice-to-haves.** Several defaults give an unauthenticated internet visitor the ability to run containers on your host. Work through this page before pointing DNS at the box.

Companion docs: [Cloud VM deployment](CLOUD_VM_DEPLOYMENT.md) · [Cloudflare DNS & SSL](CLOUDFLARE_SSL_INSTALL.md) · [Cloud VM requirements](SRS_CLOUD_VM_PLATFORM.md)

---

## What changes when you leave the laptop

| Local | Real server |
|-------|-------------|
| Only you can reach `127.0.0.1` | Anyone who finds the IP can reach every published port |
| `*.lh` resolves nowhere else | Real DNS advertises the host to the world, including scanners |
| mkcert certificates you trust yourself | Certificates must come from ACME or your CA |
| A compromise costs you a laptop | A compromise costs you the host and everything it can reach |

The dashboard mounts the **Docker socket read-write**. That is not a bug — it is how Control starts and stops your stack — but it means *anything able to reach the dashboard's control surface is root-equivalent on the host*. Every item below follows from that one fact.

---

## Findings on the current defaults

Audited against this repository. Severity is for a host with a public IP.

### 1. The Control API is unauthenticated unless you opt in — **critical**

`dashboard/control.py`:

```python
CONTROL_TOKEN = os.getenv("DASHBOARD_CONTROL_TOKEN", "").strip()

def check_control_token(request, data=None) -> bool:
    if not CONTROL_TOKEN:
        return True          # ← no token configured means every caller is authorised
```

`ecosystem-stack/services/dashboard.sh` shipped the variable **commented out** — and, worse, *never forwarded it into the container at all*. Exporting `DASHBOARD_CONTROL_TOKEN` looked like it enabled authentication while the container never saw the variable, so `control.py` kept failing open. **Fixed in this repository**: the run script now forwards the token when it is set, and behaves exactly as before when it is not.

So by default `POST /api/control` still accepts `remove`, `reset`, `deploy` and compose execution from anyone who can reach port 8090 — you must set the token.

**Fix — do this first:**

```bash
export DASHBOARD_CONTROL_TOKEN="$(openssl rand -hex 32)"
# persist it where your service manager will read it, then
./ecosystem-stack/ecosystem-stack.sh restart dashboard

# confirm it actually reached the container — an empty result means auth is still open:
docker inspect service-dashboard --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep DASHBOARD_CONTROL_TOKEN
```

Verify it is actually enforced — this must fail:

```bash
curl -s -X POST https://leco.example.com/api/control \
  -H 'Content-Type: application/json' \
  -d '{"target_id":"ai-ollama","action":"stop"}'
# expect: {"ok": false, "error": "unauthorized"}
```

If that returns anything else, stop and fix it before continuing.

### 2. The Traefik API runs with `insecure: true` — **critical**

Both `traefik/traefik-static.yaml` and `traefik/traefik-static-acme.yaml` set:

```yaml
api:
  insecure: true
```

and `traefik.sh` publishes `-p 8080:8080`, with a router at `traefik.<domain>` pointing to `api@internal`. `insecure: true` means **no authentication at all**. On a public host this hands out your complete routing table, every service name and every backend address.

**Fix:** disable the insecure API in production, or keep the dashboard but put it behind Traefik BasicAuth/forward-auth **and** stop publishing 8080 to the world. Do not rely on the port being "obscure" — it is the first thing scanned.

### 3. Every published port binds `0.0.0.0` — **high**

Observed on a running stack:

```
traefik            0.0.0.0:80->80, 0.0.0.0:443->443, 0.0.0.0:8080->8080
service-dashboard  0.0.0.0:8090->8090
leco-mcp           0.0.0.0:8099->8099
postgres           0.0.0.0:5432->5432        (services/postgres.sh)
```

Only **80** and **443** belong on a public interface. The dashboard (8090), the MCP server (8099), Traefik's API (8080) and PostgreSQL (5432) are administrative surfaces that should never be internet-reachable.

**Fix:** bind admin ports to loopback (`-p 127.0.0.1:8090:8090`) and reach them through Traefik with authentication, or over an SSH tunnel or VPN. Then enforce it at the firewall as well, because a container port publish can bypass some firewall configurations:

```bash
sudo ufw default deny incoming
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw enable
```

> Docker publishes ports by writing its own iptables rules, which can sit **in front of** UFW. Verify from *another machine* rather than trusting the config: `nmap -Pn -p 8080,8090,8099,5432 your.server`. Expect them filtered.

### 4. `cloud-install.sh` performs no hardening — **high**

The installer contains **zero** references to `DASHBOARD_CONTROL_TOKEN`, the Traefik insecure API, or any firewall step. A clean run produces a working *and* wide-open server, and nothing in the flow tells the operator that.

**Fix:** treat this page as the missing second half of the installer until it prompts for these itself.

### 5. Local-development credentials would ship as-is — **high**

The UI credential vault seeds well-known local defaults, and the file-transfer stack ships `leco#localhost-192` for SFTP and FTP (see [`FILE_TRANSFER.md`](FILE_TRANSFER.md)). These are published in this repository — they are not secrets anywhere, least of all on a public host.

**Fix:** rotate every credential in **Service hubs → UI access** before exposure, and do not enable the file-transfer stack publicly unless you have deliberately re-secured it. FTP is plaintext; do not run it on a public interface at all.

### 6. The MCP server, if exposed — **medium**

`https://mcp.<domain>/mcp` grants an agent the same 60 tools that drive your stack. Destructive tools are double-gated (`confirm=true` **and** `LECO_MCP_ALLOW_DESTRUCTIVE=1`, both off by default), which is real protection — but read tools still expose logs, routes and configuration.

**Fix:** keep `LECO_MCP_ALLOW_DESTRUCTIVE` and `LECO_MCP_ALLOW_CREDENTIALS` unset in production, set `LECO_MCP_CONTROL_TOKEN` to match the dashboard token, and put the endpoint behind authentication or a private network. For a single operator, the **stdio** transport needs no exposed port at all — prefer it.

---

## TLS on a real domain

`mkcert` is for local development only; a mkcert certificate means nothing to a visitor's browser. On a real domain use one of:

| Mode | When | Notes |
|------|------|-------|
| `acme` | You control public DNS and port 80 | Traefik obtains and renews Let's Encrypt certificates via HTTP-01 |
| `cloudflare` | Cloudflare proxies your domain | See [Cloudflare DNS & SSL](CLOUDFLARE_SSL_INSTALL.md) |
| `static` | You already have certificates | Point Traefik at the PEM pair |

Set the mode in `config/leco-platform.yaml` alongside `deployment_mode: cloud` and your `base_domain`, then apply routes (**Platform → Apply Traefik**, or `leco-devops platform traefik-apply`).

Two ACME failure modes worth pre-empting: the ACME store **must** live on a persisted volume, or every restart requests fresh certificates and you will hit Let's Encrypt's rate limits; and HTTP-01 needs port 80 reachable from the internet, so a firewall that blocks 80 breaks issuance even though your site only serves 443.

`certs/generate-certs.sh` is mkcert-only and is not part of a real-domain deployment.

---

## Pre-flight checklist

Run through this before DNS points at the host.

- [ ] `DASHBOARD_CONTROL_TOKEN` set, and an unauthenticated `POST /api/control` returns `unauthorized`
- [ ] Traefik `api.insecure` disabled, or authenticated and not published
- [ ] Only 80 and 443 reachable from outside — **verified with a port scan from another machine**
- [ ] `deployment_mode: cloud` and `base_domain` set in `config/leco-platform.yaml`
- [ ] TLS mode is `acme`, `cloudflare` or `static` — never `mkcert`
- [ ] ACME storage on a persisted volume
- [ ] Every UI-access credential rotated off its default
- [ ] File transfer (FTP/SFTP) disabled, or deliberately secured; FTP never public
- [ ] MCP destructive and credential gates unset; endpoint not publicly reachable
- [ ] Backups exist and a restore has been tested — Control `reset` deletes volumes
- [ ] Host patched, SSH key-only, root login disabled

## Things this page does not solve

Stated plainly so you do not assume coverage you do not have:

- **No multi-user model.** LEco has one shared control token, not user accounts, roles, or an audit trail of *who* acted. Anyone with the token is a full administrator. For a team, put an authenticating proxy in front and keep the token internal.
- **No rate limiting or brute-force protection** on the control surface.
- **The Docker socket is read-write** to the dashboard by design. There is no configuration that both keeps Control working and removes host-root equivalence. Treat the host as dedicated to LEco.
- **Container escape is out of scope.** Apps you onboard run as containers on the same daemon; a hostile app is a hostile neighbour to everything else on the box.
