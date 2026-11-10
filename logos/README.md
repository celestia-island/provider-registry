# Provider logos — the upstream vendor-mark cache

One normalized SVG per model provider (`<provider-id>.svg`), sourced by
`manifest.toml`, verified by `scripts/fetch_logos.py --check` and audited for
coverage by `scripts/validate_logos.py`. Consumers vendor a snapshot of this
tree (shittim-chest generates a TS module; the catalog index intentionally
does NOT embed logos — schema 1 stays closed).

## Sourcing policy (user ruling 2026-11-10)

Official first, then simple-icons, then letter fallback:

1. **official** — the vendor's press kit / brand page / site asset. Recorded
   with the exact URL. Assets that only ship inside a brand ZIP are seeded by
   hand (unzip → normalize → commit) and say so in `license`; their recorded
   `source_url` (the ZIP) will not re-fetch as SVG until `fetch_logos.py`
   grows ZIP support.
2. **simple-icons** — the official mark as a single CC0 path; pinned to the
   exact package version in `source_url` (`unpkg.com/simple-icons@<version>`,
   the mirror reachable from CN networks). The mark's colors follow the
   consumer theme via `fill=currentColor`.
3. **letter fallback** — providers with no usable vector source are listed in
   `scripts/validate_logos.py`'s `LETTER_FALLBACK` with the reason, so the gap
   stays visible instead of silent.

## Modes

- `mono` — every paint color stripped, root carries `fill=currentColor`: the
  mark follows the theme text. `brand_color` records the vendor hue for the
  consumer chip tint (required for mono entries).
- `brand` — original vendor fills kept (multi-color marks: zhipu, hunyuan,
  together).

`pre_clean = "inline-css"` resolves official Illustrator CSS-class exports
(fills on `.stN` classes) into presentation attributes before normalization.

## Refresh

```bash
python3 scripts/fetch_logos.py                # re-fetch every official/simple-icons entry
python3 scripts/fetch_logos.py --only deepseek
python3 scripts/fetch_logos.py --check        # offline: hashes + normalization invariants
python3 scripts/validate_logos.py             # + coverage audit against registry.toml
```

A fetch rewrites the derived `sha256`/`fetched_at` manifest fields; the diff
is the review artifact — a changed hash means the vendor (or simple-icons)
changed the mark, and the new bytes get rasterized and eyeballed before
commit.

## Licensing

Path data from simple-icons is CC0-1.0; official assets are the vendors'
trademarks used for vendor identification in our own tooling (nominative
use), governed by each vendor's brand guidelines. Per-provider provenance and
notes live in `manifest.toml`.
