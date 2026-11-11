# Brand assets

Sources are SVG; PNGs are rendered from them with headless Chromium so the
Inter Display wordmark renders exactly.

| File | Use |
|---|---|
| `icon.svg` | Master mark: storage box + location pin on a Home Assistant-blue tile |
| `logo.svg` / `logo-dark.svg` | Mark + wordmark for light / dark backgrounds |
| `icon-512.png` | High-resolution mark |
| `../../home_inventory/icon.png` (128×128), `logo.png` / `dark_logo.png` (250×100) | Apps store |
| `../../home_inventory/frontend/public/favicon.svg`, `apple-touch-icon.png` | Browser tab and home-screen icon |

Palette: tile `#4FC3F7 → #0277BD`, box `#FFFFFF / #E1F1FC / #B6DCF6`, pin `#FFCA28 → #FF8F00`, ink `#0B3B5E`.
The Home Assistant sidebar entry uses the Material Design icon `mdi:package-variant-closed`
(Home Assistant only allows MDI icons there).
