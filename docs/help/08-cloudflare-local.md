# Cloudflare local

Optional **KV, R2, D1** adapters on `kv.lh`, `r2.lh`, `d1.lh` for Workers development.

Start with stack or:

```bash
./ecosystem-stack/ecosystem-stack.sh start cloudflare-local
```

**Infrastructure → 4 · Cloudflare local** shows reachability.

`leco-devops provision-local-cf` creates namespaces/buckets from `wrangler.toml` bindings.

Full docs: **Docs** tab → *Cloudflare Local* section.
