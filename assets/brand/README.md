# LEco brand assets

Flat palette (no gradients): **Navy** `#022B3A` · **Teal** `#1F7A8C` · **Sky** `#BFDBF7` · **Mist** `#E1E5F2` · **White** `#FFFFFF`

### Where each color goes (light UI)

| Color | Role |
|-------|------|
| **Mist** `#E1E5F2` | Page background (main content area) |
| **White** `#FFFFFF` | Cards, inputs, inactive tabs |
| **Sky** `#BFDBF7` | Chrome accents, warning chips, secondary borders |
| **Teal** `#1F7A8C` | Primary borders, links, buttons, active tabs, OK status |
| **Navy** `#022B3A` | **Header & footer chrome**, headings, body text, strong card borders |

Header and footer use **navy background + white/sky text**. Cards and tabs use **2–4px teal/navy/sky borders** (no washed-out grey).

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
