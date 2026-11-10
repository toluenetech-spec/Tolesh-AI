# Third-party asset licence record

Part of the Priority-1 licensing audit. This is a factual inventory, not legal
advice. Where a licence text is not bundled, the official source is linked and
the gap is stated explicitly.

## 1. GSAP (bundled JavaScript)

| Field | Value |
| --- | --- |
| Asset | `web/vendor/gsap.min.js` |
| Version | GSAP 3.13.0 |
| Copyright | `Copyright 2025, GreenSock. All rights reserved.` |
| Licence | GSAP Standard "No Charge" License (licensor: Webflow) |
| Official text | <https://gsap.com/standard-license/> (also subject to <https://webflow.com/legal/terms>) |
| Bundled licence file | **No** — the official text is a web page subject to Webflow ToS; it is linked rather than copied. |
| Header preserved | **Yes** — the `/*!` banner with `@license`, `@author` and copyright is present verbatim at the top of `web/vendor/gsap.min.js`. |

Factual terms retrieved from the official page on 2026-11-10:

- **Permitted Uses**: implementation/use of GSAP on any website, web
  application or digital interface, including by companies.
- **Prohibited Uses**: use in tools that let users build visual animations
  without code where that materially assists a product competing with Webflow's
  visual animation building capabilities; reverse engineering to create
  Competitive Products; removing or altering proprietary notices or branding.
- Ownership remains with Webflow; the licence can be terminated for
  non-compliance, after which copies must be destroyed.
- The licence may be amended by posting a revised version.

Tolesh uses GSAP only for its own interface animation (`web/js/motion.js`) and
is not a no-code animation builder. **License review before any commercial
release is still required**, because this is a proprietary "no charge" licence
rather than an OSI-approved open-source licence.

## 2. Bodoni Moda (bundled webfont)

| Field | Value |
| --- | --- |
| Asset | `web/assets/fonts/aFTQ7PxzY382XsXX63LUYJ*.woff2` (4 subsets) |
| Family | Bodoni Moda, from Google Fonts |
| Copyright | `Copyright 2020 The Bodoni Moda Project Authors (https://github.com/indestructible-type/Bodoni)` |
| Licence | SIL Open Font License, Version 1.1 |
| Official text | [`OFL-Bodoni-Moda.txt`](OFL-Bodoni-Moda.txt) (fetched from `google/fonts/ofl/bodonimoda/OFL.txt`) |

## 3. Jost (bundled webfont)

| Field | Value |
| --- | --- |
| Asset | `web/assets/fonts/92zatBhPNqw73o*.woff2` (3 subsets) |
| Family | Jost, from Google Fonts |
| Copyright | `Copyright 2020 The Jost Project Authors (https://github.com/indestructible-type)` |
| Licence | SIL Open Font License, Version 1.1 |
| Official text | [`OFL-Jost.txt`](OFL-Jost.txt) (fetched from `google/fonts/ofl/jost/OFL.txt`) |

The OFL permits bundling, use and redistribution (including commercial) provided
the copyright notice and licence travel with the font files; the fonts may not
be sold by themselves; modified versions must not use the reserved font names
without permission. Tolesh ships the fonts unmodified, with the full OFL texts
now bundled in this directory. `web/styles/fonts.css` records the source and
licence family in a comment.

## 4. Python dependencies

Python dependencies are declared in [`requirements.txt`](../requirements.txt)
and installed by the user; their licences are distributed with the packages
themselves and are **not** vendored into this repository. A full dependency
licence audit (e.g. `pip-licenses`) is **outstanding** and is required before
any commercial distribution, particularly for the ML/audio stack
(faster-whisper, transformers, PyTorch, PyQt6 — note PyQt6 has GPL/commercial
dual-licensing implications).

## 5. Gaps and required actions

1. **PyQt6 dual licence** — the desktop shell imports PyQt6. Its GPL/commercial
   dual licensing must be reviewed by counsel before commercial distribution.
   No conclusion is drawn here.
2. **Dependency licence inventory** — run `pip-licenses` over the locked
   environment and record the result.
3. **GSAP licence text** — obtain and archive the applicable GSAP Standard
   License version and confirm the use case with counsel.
4. **Upstream commercial licence** — the blocking item; see
   [`../LICENSING-ASSESSMENT.md`](../LICENSING-ASSESSMENT.md).

## 6. Verification commands

```bash
# Prove the upstream licence is verbatim
diff -q LICENSE /workspace/_sources/jarvis/LICENSE
diff -q LICENSE LICENSES/isair-jarvis-LICENSE.txt

# Prove the GSAP proprietary header is intact
head -c 400 web/vendor/gsap.min.js

# Prove the font OFL texts are present
ls -1 LICENSES/OFL-*.txt
```
