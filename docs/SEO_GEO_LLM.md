# SEO, GEO, and LLM discovery (GitHub Pages)

Site: **https://leco-project.us** · Source: [`index.md`](../index.md) + Jekyll under repo root.

## SEO

| Piece | Location |
|-------|----------|
| jekyll-seo-tag | [`_layouts/default.html`](../_layouts/default.html) `{% seo %}` |
| Meta / canonical / OG | [`_includes/head-custom.html`](../_includes/head-custom.html) |
| JSON-LD | [`_includes/structured-data.html`](../_includes/structured-data.html) |
| Sitemap | [`sitemap.xml`](../sitemap.xml) built from [`_data/sitemap.yml`](../_data/sitemap.yml) (home + LLM discovery files) |
| `robots.txt` | [`robots.txt`](../robots.txt) |
| Social image | [`assets/img/leco-og.svg`](../assets/img/leco-og.svg) |

## GEO (generative engine optimization)

| File | URL |
|------|-----|
| `llms.txt` | https://leco-project.us/llms.txt |
| `llms-full.txt` | https://leco-project.us/llms-full.txt |
| `ai.txt` | https://leco-project.us/ai.txt |
| FAQ (HTML + schema) | https://leco-project.us/#faq — data in [`_data/faqs.yml`](../_data/faqs.yml) |

## Google Analytics / Tag Manager

Set in [`_config.yml`](../_config.yml) (empty = disabled):

```yaml
google_analytics: "G-XXXXXXXXXX"
google_tag_manager: "GTM-XXXXXXX"
google_site_verification: "verification-token"
```

Rendered by [`_includes/analytics.html`](../_includes/analytics.html).

## Updating FAQs

Edit [`_data/faqs.yml`](../_data/faqs.yml) — the landing FAQ section and FAQPage JSON-LD update on the next Pages build.
