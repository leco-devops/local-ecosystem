# LEco brand assets

Flat palette (no gradients): **Navy** `#022B3A` · **Teal** `#1F7A8C` · **Sky** `#BFDBF7` · **Mist** `#E1E5F2` · **White** `#FFFFFF`

### Where each color goes (light UI)

| Color | Role |
|-------|------|
| **Mist** `#E1E5F2` | Page background |
| **White** `#FFFFFF` | Cards, header, inputs, footer panels |
| **Sky** `#BFDBF7` | Borders, zebra rows, muted bands, warning badge background |
| **Teal** `#1F7A8C` | Links, primary buttons, active tabs, success badges, chart accents |
| **Navy** `#022B3A` | Headings and body text, strong borders, danger/degraded badges |

Tokens live in [`leco-palette.css`](leco-palette.css) (imported by dashboard and GitHub Pages).

| File | Use |
|------|-----|
| `leco-logo-mark-dark.svg` / `leco-logo-mark-light.svg` | Icon / favicon (dark UI vs light backgrounds) |
| `leco-logo-dark.svg` / `leco-logo-light.svg` | Horizontal lockup |
| `leco-logo-round-dark.svg` / `leco-logo-round-light.svg` | Profile / social avatar (SVG) |
| `leco-logo-round.png` | PNG avatar (regenerate from round-dark when palette changes) |
| `leco-palette.css` | Shared CSS variables |

Default filenames (`leco-logo-mark.svg`, `leco-logo.svg`, …) are **dark** variants for the site and dashboard.

Copies: `dashboard/static/`, `assets/img/`.
