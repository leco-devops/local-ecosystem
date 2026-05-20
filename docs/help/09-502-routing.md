# 502 / routing / `lh-network`

## Symptom

`https://myapp.lh` → **502 Bad Gateway** or connection reset.

## Checklist

1. **Backend container running**
   ```bash
   docker ps --filter name=myapp
   ```
2. **On `lh-network`**
   ```bash
   docker network connect lh-network myapp-server
   ```
   Or add `lh-network` external network in compose.
3. **Traefik service URL** matches Docker DNS name + port in `hosting/traefik/dynamic.yml`.
4. **Host rule** matches browser hostname (`Host(\`myapp.lh\`)`).
5. Heal stack routes:
   ```bash
   ./ecosystem-stack/ecosystem-stack.sh heal traefik
   ```

## AirLLM / Ollama specific

- Container healthy? `docker ps --filter name=airllm`
- In-container API: `curl -H "Host: airllm.lh" http://127.0.0.1/health`
- Shim must bind `0.0.0.0:11435` (not `127.0.0.1` only).

See **Docs** → *Hosted apps Traefik runbook*.

## Related: Varnish 503

If the error page says **Varnish cache server** and **503 Backend fetch failed**, the issue is between **Varnish and Express**, not Traefik routing. See [503 / Varnish backend](help:ts-503).
