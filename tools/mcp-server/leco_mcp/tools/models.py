"""Local LLM runtimes: Ollama (GGUF) and AirLLM (large HF models, layer streaming)."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..runtime import Deps
from ..safety import guard_explicit_destructive
from ..shaping import guard_size, pick

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)

RUNTIMES = ("ollama", "airllm")

MODEL_ACTIONS = frozenset({"pull", "pull_all", "delete", "unload", "on", "off"})


def _runtime(name: str) -> str:
    r = (name or "").strip().lower()
    if r not in RUNTIMES:
        raise ValueError(f'runtime must be one of {", ".join(RUNTIMES)}.')
    return r


def register(server: MCPServer, deps: Deps) -> None:
    client = deps.client
    settings = deps.settings

    @server.tool(name="leco_llm_models", annotations=READ_ONLY)
    async def leco_llm_models(
        runtime: Literal["ollama", "airllm"] = "ollama",
        installed_only: bool = False,
    ) -> dict[str, Any]:
        """Installed and pinned models for a local LLM runtime, with load state.

        Rows show whether a model is installed, pinned (declared in the seed catalog), and
        currently resident in memory, plus size and quantization.
        """
        r = _runtime(runtime)
        payload = await client.get(f"/api/{r}/models")
        rows = payload.get("rows") or []
        if installed_only:
            rows = [x for x in rows if x.get("installed")]
        return guard_size(
            {
                "runtime": r,
                "reachable": payload.get(f"{r}_reachable"),
                "base_url": payload.get(f"{r}_base"),
                "server_version": payload.get("server_version"),
                "installed_count": payload.get("installed_count"),
                "running_count": payload.get("running_count"),
                "models": [
                    pick(
                        x,
                        "name",
                        "canonical",
                        "installed",
                        "pinned",
                        "running",
                        "size",
                        "parameter_size",
                        "quantization_level",
                        "model_family",
                        "modified_at",
                    )
                    for x in rows
                ],
            },
            settings.max_response_chars,
        )

    @server.tool(name="leco_llm_model_action", annotations=WRITES)
    async def leco_llm_model_action(
        action: Literal["pull", "pull_all", "delete", "unload"],
        model: str = "",
        runtime: Literal["ollama", "airllm"] = "ollama",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Pull, delete, or unload a local model.

        pull      download a model (starts in the background; poll leco_llm_models)
        pull_all  download every pinned model from the seed catalog
        unload    evict a model from memory without deleting it
        delete    remove the model from disk — requires confirm=true and
                  LECO_MCP_ALLOW_DESTRUCTIVE=1

        model is required for everything except pull_all.
        """
        r = _runtime(runtime)
        act = (action or "").strip().lower()
        if act not in MODEL_ACTIONS:
            raise ValueError(f'Unknown action {action!r}. Allowed: {", ".join(sorted(MODEL_ACTIONS))}.')
        if act != "pull_all" and not model.strip():
            raise ValueError("model is required for this action.")
        if act == "delete":
            guard_explicit_destructive(
                settings, confirm, what=f"delete {r} model {model!r} from disk"
            )
        return guard_size(
            await client.post(
                f"/api/{r}/models/action",
                json_body={"action": act, "model": model.strip()},
                timeout=settings.action_timeout,
            ),
            settings.max_response_chars,
        )

    @server.tool(name="leco_llm_model_inspect", annotations=READ_ONLY)
    async def leco_llm_model_inspect(
        model: str, runtime: Literal["ollama", "airllm"] = "ollama"
    ) -> dict[str, Any]:
        """Manifest, layers, template, and parameters for one installed model."""
        r = _runtime(runtime)
        return guard_size(
            await client.get(f"/api/{r}/model/inspect", params={"model": model.strip()}),
            settings.max_response_chars,
        )

    @server.tool(name="leco_llm_catalog", annotations=READ_ONLY)
    async def leco_llm_catalog(
        runtime: Literal["ollama", "airllm"] = "ollama",
        query: str = "",
        limit: int = 40,
    ) -> dict[str, Any]:
        """Browse the tracked upstream model catalog (what you could install).

        query filters by model name substring. This is the catalog the update-catalog
        watcher refreshes; it is not the list of what is installed (use leco_llm_models).
        """
        r = _runtime(runtime)
        payload = await client.get(f"/api/llm-catalog/{r}")
        items = payload.get("models") or payload.get("items") or payload.get("rows") or []
        if query:
            q = query.lower()
            items = [x for x in items if q in str(x.get("name") or x.get("id") or "").lower()]
        return guard_size(
            {
                "runtime": r,
                "generated_at": payload.get("generated_at"),
                "total": len(items),
                "models": items[: max(1, min(int(limit), 200))],
            },
            settings.max_response_chars,
        )
