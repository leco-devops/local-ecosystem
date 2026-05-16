# Common issues

## Dashboard shows old UI (no Model manager / Popular dropdown)

1. Restart dashboard container:
   ```bash
   ./ecosystem-stack/ecosystem-stack.sh restart dashboard
   ```
2. Hard refresh browser (`Cmd+Shift+R`).
3. Confirm `dashboard.js?v=…` query string changed in page source.

## `airllm` container exits immediately

```bash
docker logs airllm
```

Common causes (fixed in current repo pins):

- `optimum.bettertransformer` missing → pin `optimum<1.18`
- `transformers.utils.is_tf_available` → pin `transformers<4.49`
- Rebuild: `AIRLLM_FORCE_BUILD=1 ./leco-cli.sh airllm build`

## Ollama / AirLLM model actions return unauthorized

Set **Control token** on Control tab to match `DASHBOARD_CONTROL_TOKEN` in `dashboard.sh`.

## Help page 404

Ensure dashboard image includes `docs/help/` mount. Restart dashboard after adding help files.

## More

- [502 / routing](help:ts-502)
- [Removal](help:removal)
