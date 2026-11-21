# Provider logos — the upstream vendor-mark cache

One normalized SVG per model provider (`<provider-id>.svg`), sourced by
`manifest.toml`, verified by `scripts/fetch_logos.py --check` and audited for
coverage by `scripts/validate_logos.py`. Consumers vendor a snapshot of this
tree (shittim-chest generates a TS module; the catalog index intentionally
does NOT embed logos — schema 1 stays closed).

## Sourcing policy (user rulings 2026-11-10 / 2026-11-11)

A verified, current first-party mark wins over third-party renditions;
otherwise use the icon library, simple-icons, then letter fallback:

1. **official** — the vendor's press kit / brand page / site asset. This
   priority is deliberate: a familiar third-party approximation must not
   replace the vendor's own identity. Z.ai therefore uses the official 30×30
   dark-plate/white-Z asset in `brand` mode. Assets that only ship inside a
   brand ZIP are seeded by hand (unzip → normalize → commit) and say so in
   `license`; their recorded `source_url` (the ZIP) will not re-fetch as SVG
   until `fetch_logos.py` grows ZIP support.
2. **icon library** — the dedicated AI-provider library
   (`@lobehub/icons-static-svg`, MIT, community-maintained marks derived from
   the official brand assets), pinned to the exact package version in
   `source_url`. It covers providers without a verified official SVG. Its
   entries share one 24×24 mono `currentColor` grammar, apart from doubao's
   documented color mark, so chips remain visually consistent and theme-safe.
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
- `brand` — original vendor fills kept (doubao's color cloud and Z.ai's
  dark-plate/white-Z official mark; 20 mono + 2 brand entries).

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

## Combine lockups (`logos/combine/`)

`scripts/fetch_logos_combines.py` composes one lockup per provider — the
vendor mark plus the vendor wordmark in one coordinate system — as the
"brand row" face for consumer model rows. The mono tree above is untouched:
it stays the fallback and compact-face asset. The per-provider
`[<id>.combine]` manifest tables are the hand-authored mapping (registry id →
the pinned library's icon stems, e.g. `moonshot` → `kimi`, `xai` → `grok`,
`zhipu_glm` → `zai`); the tool owns `sha256`/`fetched_at` exactly like the
mono fetcher, and the same re-run discipline applies: unchanged sources are
byte-identical, so a hash only moves when the library moves.

Each committed file carries three layers (`pr-combine-mark-color`,
`pr-combine-mark-mono`, `pr-combine-text`), each stamped with its placed box
(`data-pr-box="x y w h"` — always inside the canvas, the validator pins it)
so a consumer can split a layer without re-measuring. Geometry: one lockup
unit = the mark ink height — a tall mark (ink aspect ≤ 1) fills the unit
height and an over-wide mark keeps its aspect at the unit width (the viewBox
is pinned to `0 0 W 1` and nothing escapes it); the wordmark's measured ink
band maps to 1/1.45 of the unit height, vertically centered — so only a
tall mark stands the skins' 1.45:1 proportion, a clamped wide mark rides
lower; the mark↔word gap is 0.3 cap units (0.3/1.45 in lockup units); the
clamp is uniform scale, never distortion. Ink boxes are measured with
fontTools by parsing the SVG path data into a bounds pen — no rasterization,
no font files.

Layer policy (the readability contract, shared with the consumer skins):

- **Color layer** — the vendor's `-color` artwork when the library ships
  one. The single most-carried flat fill (the "main" fill) is
  contrast-checked against the light fabric `#ffffff`: below the 1.5 art
  floor (effectively invisible) it is rewritten to `currentColor` — tripo's
  pale yellow (1.41) flips, while perplexity's teal (2.38) and claude's
  coral (3.12) are brand hues and survive. Gradient paints are kept
  verbatim — their own light-to-dark span carries the shape's readability,
  and rewriting one would erase multi-color identities (the Gemini spark).
  Knockout accents are never rewritten: only the main flat fill is touched.
- **Without `-color` artwork** — the color layer is the mono mark tinted
  with the brand hex (`tint` override, else `brand_color`), but only when
  the tint itself clears the stricter 3.0 tint floor on white (openai/
  runway/xai/zai tint; mimo's `#FF6900` misses at 2.89, so its color layer
  folds into the mono layer — which then carries both classes, mirroring
  the consumer skins' contract). The two floors are different jobs: the art
  floor keeps a brand hue that still reads; the tint floor refuses a tint
  that would BE the whole layer and not read.
- **Mono layer / text layer** — always `currentColor`, following the
  consumer's theme text. A vendor whose `-text` variant is missing is
  SKIPPED (no lockup synthesized, the gap named in the run report) — a
  lockup without a wordmark is not faked.

A vendor whose mark and color artwork draw slightly differently is placed by
the union of both ink boxes (each layer contain-fitted, never stretched);
named colors in color artwork are canonicalized to hex (mistral's `gold`).
Mechanical validation runs on every composed file (no active content, no
external references, pinned viewBox, paint allowlist, 64 KiB cap) and
`--check` re-verifies hashes + invariants offline; `validate_logos.py`
audits the tree's hashes too.

Refresh:

```bash
# fontTools lives in a project venv (not the system interpreter):
python3 -m venv .venv-combine && .venv-combine/bin/pip install "fonttools>=4.43"
.venv-combine/bin/python scripts/fetch_logos_combines.py          # network
.venv-combine/bin/python scripts/fetch_logos_combines.py --only deepseek
.venv-combine/bin/python scripts/fetch_logos_combines.py --check  # offline
```

Known limitations: upstream gradient `id`s (`lobe-icons-<brand>-…`) are
document-global in HTML, so several inlined copies of the SAME vendor's
color layer on one page reuse an id — the referenced gradient is identical,
so rendering is unaffected (the mono tree's "no url() references" property
does not hold for the color layers, which is why the composer validates
every reference as an internal fragment only). Named colors outside the
composer's minimal table fail the run loudly instead of shipping unmeasured.

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
  document. The mono tree has zero `url(` references (grep-checked at
  sourcing time); the COMBINE color layers deliberately do carry internal
  gradient references (upstream `lobe-icons-<brand>-*` ids, kept vendor-
  namespaced) — see the combine section above for the collision analysis.
  Id rewriting was deliberately not added — it risks breaking refs for a
  collision that does not exist. Revisit if an asset lands with a
  suspiciously generic id (`Layer_1`, `clip0`).
