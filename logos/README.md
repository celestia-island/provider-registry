# Provider logos — the upstream vendor-mark cache

One normalized SVG per model provider (`<provider-id>.svg`), sourced by
`manifest.toml`, verified by `scripts/fetch_logos.py --check` and audited for
coverage by `scripts/validate_logos.py`. Consumers vendor a snapshot of this
tree (shittim-chest generates a TS module; the catalog index intentionally
does NOT embed logos — schema 1 stays closed).

## Sourcing policy (user rulings 2026-11-10)

Icon library first, then official, then simple-icons, then letter fallback:

1. **icon library** — the dedicated AI-provider library
   (`@lobehub/icons-static-svg`, MIT, community-maintained marks derived from
   the official brand assets), pinned to the exact package version in
   `source_url`. The whole tree is lobehub-sourced (pinned 1.102.0): one
   uniform 24×24 set, 21 of 22 in mono `currentColor` (the one exception is
   doubao, whose identity is its color — see Modes), so chips share one
   visual language and render theme-safely apart from that documented case.
2. **official** — the vendor's press kit / brand page / site asset, for
   providers the library does not cover or whose identity is color-centric.
   Assets that only ship inside a brand ZIP are seeded by hand (unzip →
   normalize → commit) and say so in `license`; their recorded `source_url`
   (the ZIP) will not re-fetch as SVG until `fetch_logos.py` grows ZIP
   support.
3. **simple-icons** — the official mark as a single CC0 path; pinned to the
   exact package version in `source_url`.
4. **letter fallback** — providers with no usable vector source are listed
   in `scripts/validate_logos.py`'s `LETTER_FALLBACK` with the reason, so
   the gap stays visible instead of silent. **Relay/reseller providers
   (fireworks, openrouter, silicon/SiliconFlow, together) carry no mark by
   the 2026-11-10 ruling: the models they serve render the ACTUAL
   DEVELOPER's mark via the consumer's model-family mapping, and
   relay-exclusive models render bare.**

## Modes

- `mono` — every paint color stripped, root carries `fill=currentColor`: the
  mark follows the theme text. `brand_color` records the vendor hue for the
  consumer chip tint (required for mono entries).
- `brand` — original vendor fills kept (currently only doubao, whose cloud
  identity is its color).

`pre_clean = "inline-css"` resolves `<style>` CLASS rules into presentation
attributes before that walk — official Illustrator exports put every fill
on `.stN` classes, and stripping the block would leave unpainted shapes.
Only plain `.class` selectors are accepted; anything structural fails
loudly instead of half-converting.

## Refresh

```bash
python3 scripts/fetch_logos.py                # re-fetch every entry (network)
python3 scripts/fetch_logos.py --only deepseek
python3 scripts/fetch_logos.py --check        # offline: hashes + normalization invariants
python3 scripts/validate_logos.py             # + coverage audit against registry.toml
```

A fetch rewrites the derived `sha256`/`fetched_at` manifest fields; the diff
is the review artifact — a changed hash means the vendor (or the library)
changed the mark, and the new bytes get rasterized and eyeballed before
commit.

## Licensing

The lobehub library is MIT; simple-icons path data is CC0-1.0; official
assets are the vendors' trademarks used for vendor identification in our
own tooling (nominative use), governed by each vendor's brand guidelines.
Per-provider provenance and notes live in `manifest.toml`.

## Known limitations

- **Relay/reseller providers carry no mark** (see the sourcing policy):
  fireworks, openrouter, silicon/SiliconFlow and together are LETTER_FALLBACK
  rows; their exclusive models render bare and their served models render
  the developer's mark. If a relay ships its own R&D line, move it into
  `manifest.toml` with a note.
- **Document-level ids are not namespaced.** A future asset carrying
  internal `url(#id)` refs could collide across vendors sharing one
  document. Today's tree has zero `url(` references (grep-checked at
  sourcing time); id rewriting was deliberately not added — it risks
  breaking refs for a collision that does not exist. Revisit if an asset
  lands with a suspiciously generic id (`Layer_1`, `clip0`).
