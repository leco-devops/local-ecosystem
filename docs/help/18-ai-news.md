# AI news aggregator

The **Develop** tab includes a live **AI news** panel that pulls headlines from configured RSS/Atom feeds.

## Features

- **Categories** — research, products, open-source, local-llm, tools, policy (configurable).
- **Tags** — per-feed defaults plus auto-tagging from keyword hints.
- **Search** — filter title and summary text.
- **Local LLM refine** — describe your interest; Ollama suggests categories, tags, and keywords (`POST /api/ai-news/refine`).

## Configuration

Edit `ecosystem-stack/config/ai-news-sources.json` to add or remove feeds. Cache TTL defaults to 15 minutes (`cache_ttl_seconds`).

## API

- `GET /api/ai-news?category=&tags=&q=&refresh=1`
- `POST /api/ai-news/refine` — body `{ "query": "…" }`

Requires **Ollama** running for LLM suggestions (Infrastructure → Ollama).
