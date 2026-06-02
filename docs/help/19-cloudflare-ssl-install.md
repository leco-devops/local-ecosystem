# Cloud install — Cloudflare DNS & SSL

Use this guide when LEco runs on a **cloud VM** with a real domain (e.g. `*.mydomain.com`) and **Cloudflare manages HTTPS** at the edge.

Full walkthrough: [CLOUDFLARE_SSL_INSTALL.md](../CLOUDFLARE_SSL_INSTALL.md).

---

## Quick checklist

1. **Cloudflare DNS** — proxied wildcard `*` → VM public IP  
2. **Cloudflare SSL** — **Flexible** (simplest: HTTPS to visitors, HTTP to VM on port 80)  
3. **Install** — `./ecosystem-stack/cloud-install.sh --profile ai-full --domain mydomain.com --tls cloudflare`  
4. **Platform config** — `deployment_mode: cloud`, `base_domain: mydomain.com`, `tls.mode: cloudflare`  
5. **Traefik routes** — `python3 scripts/render-platform-traefik.py --write` then `traefik.sh heal`  
6. **Start** — `./ecosystem-stack/ecosystem-stack.sh start`  
7. **Open** — `https://dashboard.mydomain.com`

---

## Hostname mapping

LEco replaces `.lh` with your `base_domain`:

| Local | Cloud |
|-------|-------|
| `dashboard.lh` | `dashboard.mydomain.com` |
| `paperclip.lh` | `paperclip.mydomain.com` |
| `<stackId>.lh` | `<stackId>.mydomain.com` |

Set `base_domain: leco.mydomain.com` if you want `dashboard.leco.mydomain.com` instead.

---

## Paperclip on cloud

```bash
export PAPERCLIP_PUBLIC_URL=https://paperclip.mydomain.com
./ecosystem-stack/ecosystem-stack.sh paperclip restart
```

Agent LLM adapters inside Paperclip still use `http://ollama:11434` and `http://airllm:11435` (Docker DNS).

---

## Platform tab

After install, use **Platform** to edit `leco-platform.yaml`, start/stop bundles, build dev stacks, and **Apply Traefik routes**.

See [Platform tab & dev stacks](help:dash-platform).

---

## Related

- [Cloud VM deployment](help:cloud-vm-deployment) — profiles and all TLS modes  
- [Paperclip](help:paperclip) — agent orchestration  
- [CLOUDFLARE_SSL_INSTALL.md](../CLOUDFLARE_SSL_INSTALL.md) — full install README
