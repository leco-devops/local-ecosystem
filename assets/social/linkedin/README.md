# LinkedIn article assets — LEco DevOps

Everything needed to publish the LEco DevOps article on LinkedIn.

## Publish in this order

1. **Cover** — upload `01-banner-cover.png` as the article cover image.
2. **Title** — the title line is at the top of `LINKEDIN-ARTICLE.txt` (82 of LinkedIn's 100 characters).
3. **Body** — two ways, pick one:
   - **Formatted (recommended):** open `LINKEDIN-ARTICLE.html` in a browser, press
     *Select the article body*, copy, paste into LinkedIn. Headings, bold and lists survive.
   - **Plain:** copy the text between the ▼ and ▲ markers in `LINKEDIN-ARTICLE.txt`,
     then apply Heading 2 to the heading lines with LinkedIn's toolbar.
4. **Images** — the body carries five placeholder lines, `[ IMAGE → <file>.png ]`, each with the
   caption to use. Delete the placeholder, insert the named image there, paste the caption.
   Alt text for every image is at the bottom of `LINKEDIN-POSTS.txt`.
5. **Share it** — `LINKEDIN-POSTS.txt` has two ready feed posts to link the article from.

## Files

| File | Use | Size |
|---|---|---|
| `LINKEDIN-ARTICLE.html` | Pre-formatted paste source — open, select, copy | — |
| `LINKEDIN-ARTICLE.txt` | Same article as plain text, with image placeholders | ~1,400 words |
| `LINKEDIN-POSTS.txt` | Two companion feed posts + alt text for every image | — |
| `preview.html` | Reading preview with all diagrams inline (review only, do not paste) | — |
| `01-banner-cover.png` | Article cover / banner | 1920 × 1080 |
| `02-banner-wide.png` | Wide variant — repo social preview, profile header | 2256 × 752 |
| `03-data-flow.png` | Data flow | 1600 × 1180 |
| `04-runtime-topology.png` | Runtime topology | 1600 × 940 |
| `05-read-write.png` | Read / write boundary | 1600 × 900 |
| `06-capability-map.png` | Capability map | 1600 × 900 |
| `07-before-after.png` | Before / after | 1600 × 740 |

Each PNG has its `.svg` source beside it. Colours and the logo mark come from
`assets/brand/leco-logo.svg` and `assets/css/leco.css` — flat, no gradients, matching the
project's brand decision.

Re-render after editing an SVG:

```bash
rsvg-convert -w 1600 03-data-flow.svg -o 03-data-flow.png
```
