# Tolesh — Licensing Assessment (Priority 1)

> **Status: BLOCKER IDENTIFIED AND DOCUMENTED.**
> This is a factual audit of licence texts and inherited code, **not legal
> advice**. Tolesh is **not** claimed to be commercially distributable. No
> copyright or attribution notice was removed; several that were missing have
> been restored.

## 1. Executive finding

**Commercial distribution is not permitted under the licence Tolesh currently
inherits.** A separate, written commercial licence from the copyright holder,
**Baris Sencan (isair)**, is required before any commercial distribution. The
upstream licence states this explicitly and also requires that derivative works
carry the same terms.

| Question | Answer |
| --- | --- |
| What is the exact upstream licence? | "Jarvis AI Assistant License", Copyright (c) 2025 Baris Sencan — a **custom non-commercial** licence |
| Is personal / educational / research use allowed? | Yes, free of charge, subject to retaining notices |
| Is selling, renting, leasing or commercialising allowed? | **No** — expressly prohibited by condition 2 |
| Does the restriction apply to derivatives? | **Yes** — condition 3: "Any derivative works are also licensed under these same terms" |
| Is commercial distribution permitted today? | **No. Not without a separate commercial licence.** |
| What is required? | A separate commercial licence from the copyright holder; contact **baris@writeme.com** (from the licence text) |
| Is Tolesh claimed to be commercially distributable? | **No.** Explicitly not claimed anywhere. |

## 2. Method — commands actually run

```bash
# Exact upstream licence text, verbatim
cat LICENSES/isair-jarvis-LICENSE.txt
cat /workspace/_sources/jarvis/LICENSE

# Is the archived copy identical to the upstream original?
diff -q /workspace/_sources/jarvis/LICENSE LICENSES/isair-jarvis-LICENSE.txt
# -> IDENTICAL

# Upstream README statement
grep -niE "licen[sc]e|commercial|non-commercial" /workspace/_sources/jarvis/README.md
# -> "Jarvis is free for personal use. For commercial use, get in touch (baris@writeme.com)."

# Inherited-code and attribution scan
grep -rniE "copyright|©|attribution" src/ website/ web/ *.md

# Root licence / website link
ls LICENSE*
grep -n "license" website/build.py   # -> source_url + '/LICENSE'
```

The full upstream licence text (38 lines) is reproduced verbatim in the root
[`LICENSE`](LICENSE) and in
[`LICENSES/isair-jarvis-LICENSE.txt`](LICENSES/isair-jarvis-LICENSE.txt).

## 3. The governing terms, quoted

From `LICENSE` (verbatim):

> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to use,
> copy, modify, merge, publish, and distribute the Software for **non-commercial
> purposes**, subject to the following conditions:

> **NON-COMMERCIAL USE:**
> 1. The above copyright notice and this permission notice appear in all copies.
> 2. You do not sell, rent, lease, or otherwise commercialise the Software.
> 3. Any derivative works are also licensed under these same terms.

> **COMMERCIAL USE:**
> Commercial use of the Software requires a separate commercial license from
> the copyright holder. Commercial use includes, but is not limited to:
> - Using the Software in a commercial product or service
> - Using the Software to provide paid services
> - Distributing the Software as part of a commercial offering
> - Using the Software in any revenue-generating activity
>
> To obtain a commercial license, please contact: [baris@writeme.com]

## 4. What was inherited

Tolesh was created by copying the `isair/jarvis` tree **verbatim**, rebranding
479 files, and adding new modules in parallel namespaces. It is therefore a
derivative work in the ordinary sense of the term, and condition 3 applies.
Inherited and still-present subsystems include the PyQt6 desktop shell,
listening/wake-word/VAD, dictation, output/TTS, memory graph, reply engine,
tools/MCP, config, evals and the 5,900+ test suite.

**Copyright/attribution notices found intact:**

- Upstream licence + `Copyright (c) 2025 Baris Sencan` — archived in
  `LICENSES/` (verified byte-identical to the upstream `LICENSE`).
- Upstream README / AGENTS / EVALS — archived unchanged in `docs/upstream/`.
- `website/privacy.html` — names Baris Sencan (isair), FatihMakes and both
  upstream projects as the source of the derivative.
- `website/site.json` — `upstream_repository` and author metadata.
- `docs/img/upstream-face.png` — the original illustration preserved alongside
  the re-graded `docs/img/face.png`.
- `web/vendor/gsap.min.js` — the proprietary GSAP header preserved verbatim.
- `web/styles/fonts.css` — comment recording the Google Fonts / SIL OFL source.

**No notice was removed during this audit.** Four gaps were found and filled
(see §6).

## 5. All three upstream repositories

| Repository | Licence | Commercial use | Used as code base? |
| --- | --- | --- | --- |
| `isair/jarvis` | Custom **non-commercial** "Jarvis AI Assistant License" | Requires separate licence from Baris Sencan | **Yes — the foundation** |
| `FatihMakes/Mark-LV` | **CC BY-NC 4.0** | Prohibited | No — concept only, no code copied |
| `jev-chat/jev-chat-jarvis` | **MIT** (+ NOTICE requiring source credit) | Permitted | No — inspected only |

Note the earlier assessment in `ARCHITECTURE.md` described the selected
foundation as *"permissive, attribution"*. **That was incorrect** and has been
corrected (§6). The jev-chat-jarvis MIT licence does **not** help here: Tolesh
is not derived from that repository, so its permissive terms do not govern this
codebase.

## 6. Errors found and corrected in this audit

| # | Problem | Correction |
| --- | --- | --- |
| 1 | `README.md`, `ARCHITECTURE.md`, `IMPLEMENTATION-REPORT.md` described the foundation licence as "Custom ... (permissive, attribution)" / "Custom permissive attribution licence" | Rewritten to state **non-commercial**, derivatives inherit the same terms, commercial use requires a separate licence |
| 2 | `ARCHITECTURE.md` claimed Mark-LV was unusable as a base *because* of its non-commercial licence, while implying the selected foundation was fine for commercialisation | Corrected: the selected foundation is **also** non-commercial; the distinction is that Mark-LV's code was never copied |
| 3 | No root `LICENSE` file existed, yet `website/build.py` links `$license` to `{repository}/LICENSE` | Created root [`LICENSE`](LICENSE) as a byte-identical copy of the upstream licence. **The local target now exists, but the remote link still returns 404** — see item 7 and §9 |
| 4 | `website/privacy.html` referenced `ATTRIBUTION.md`, which did not exist | Created [`ATTRIBUTION.md`](ATTRIBUTION.md) with the full notice inventory |
| 5 | Bundled `Bodoni Moda` and `Jost` woff2 fonts had no accompanying OFL text | Fetched the official Google Fonts OFL texts into `LICENSES/OFL-Bodoni-Moda.txt` and `LICENSES/OFL-Jost.txt` (both include the original project copyright lines) |
| 6 | No inventory of third-party asset licences | Created [`LICENSES/THIRD-PARTY-ASSETS.md`](LICENSES/THIRD-PARTY-ASSETS.md) covering GSAP 3.13.0, both fonts and the unbundled Python dependency stack |
| 7 | Generated public-site licence link `https://github.com/tolesh-ai/tolesh/blob/main/LICENSE` returns **404** on both `main` and `master` | **Not fixed** — investigated, not guessed. See the §6b investigation below. `site.json` was deliberately left unchanged because no correct published destination could be confirmed |

### §6b. Remote 404 investigation (verbatim evidence)

The link was generated by `website/build.py`:
`source_url = site['repository'] + '/blob/' + site['source_branch']`,
then `license = source_url + '/LICENSE'`. With
`repository = https://github.com/tolesh-ai/tolesh` and
`source_branch = main`, the generated target is
`https://github.com/tolesh-ai/tolesh/blob/main/LICENSE`.

Actual probes (2026-11-10):

```
git remote -v                                      -> (empty, no remote configured)
git branch -a                                      -> * master   (1 commit, 716a1ae)
curl -D - https://api.github.com/repos/tolesh-ai/tolesh
    HTTP/2 404 ; {"message":"Not Found"}
    x-ratelimit-limit: 60 ; x-ratelimit-remaining: 59   <-- NOT rate-limited
curl -sS https://api.github.com/rate_limit       -> core limit/remaining/used: 60 58 2
curl -o /dev/null -w %{http_code} -L https://github.com/tolesh-ai/tolesh       -> 404
curl -o /dev/null -w %{http_code} -L https://api.github.com/users/tolesh-ai    -> 404
curl -o /dev/null -w %{http_code} -L .../blob/master/LICENSE                   -> 404
git ls-remote https://github.com/tolesh-ai/tolesh
    fatal: could not read Username for 'https://github.com': No such device or address
```

**Root cause:** the repository (and the `tolesh-ai` namespace itself) named in
`website/site.json` does not exist / has never been published. This is **not**
a branch-name problem, not a file-path problem, and not GitHub rate limiting.
The local [`LICENSE`](LICENSE) now exists, so the link would resolve once that
repository is published — but no published destination exists today.

**Decision:** `site.json` was **not** modified. The instruction was to fix it
only after confirming the correct published destination, and no such
destination could be confirmed. Guessing an alternative URL is explicitly
disallowed. The same unpublished repository is also referenced by
`src/desktop_app/app.py`, `src/desktop_app/issue_report.py` (issue-report
links). `site.json` additionally says `main` while this working copy is on
`master`; that mismatch was recorded, not silently changed.

`website/privacy.html` mentions `LICENSES/` and `ATTRIBUTION.md` as **plain
text, not hyperlinks**, so there is no attribution URL on the generated site
to validate; both referenced files now exist in the repository.

The `website/` pages themselves were already correct — `privacy.html` says
*"custom non-commercial licence ... Commercial use requires a separate licence
from the copyright holder"*, `home.html`/`layout.html`/`getting-started.html`
say *"Free for non-commercial use"*. The inconsistency was in the root
developer documents.

### §6c. Full generated-output link verification (repeat, 2026-11-10)

The site was rebuilt verbatim and **every** absolute URL in the generated
output was validated — 16 unique URLs across `index.html`,
`getting-started/index.html`, `privacy/index.html`, `sitemap.xml` — not only
`$license`.

```
rm -rf /tmp/tolesh-site-check
python3.11 website/build.py --output /tmp/tolesh-site-check
✅ Built 3 public pages in /tmp/tolesh-site-check
   🌐 Canonical base: https://barissencan.com/tolesh/
```

HTTP results (`curl -sS -L -o /dev/null -w '%{http_code}' --max-time 20`):

| Status | URL | Role |
| --- | --- | --- |
| 200 | `http://www.sitemaps.org/schemas/sitemap/0.9` | sitemap namespace |
| 200 | `https://schema.org` | JSON-LD vocabulary |
| 200 | `https://github.com/isair/jarvis` | upstream attribution |
| 200 | `https://github.com/isair` | author attribution |
| 200 | `https://github.com/FatihMakes/Mark-LV` | concept attribution |
| 404 | `https://github.com/tolesh-ai/tolesh/blob/main/LICENSE` | **$license** (3 pages + JSON-LD) |
| 404 | `https://github.com/tolesh-ai/tolesh` | `$source` / repository |
| 404 | `https://github.com/tolesh-ai/tolesh/blob/main/EVALS.md` | privacy page |
| 404 | `https://github.com/tolesh-ai/tolesh/blob/main/README.md#known-limitations` | privacy page |
| 404 | `https://github.com/tolesh-ai/tolesh/blob/main/docs/CONFIGURATION.md` | getting-started |
| 404 | `https://github.com/tolesh-ai/tolesh/issues` | privacy page |
| 404 | `https://github.com/tolesh-ai/tolesh/releases` | download links |
| 404 | `https://barissencan.com/tolesh/` | canonical base |
| 404 | `https://barissencan.com/tolesh/assets/face.png` | canonical asset |
| 404 | `https://barissencan.com/tolesh/getting-started/` | canonical page |
| 404 | `https://barissencan.com/tolesh/privacy/` | canonical page |

Findings:

- **All 11 `404`s trace to two external facts**, not to anything fixable in this
  working copy: (a) the `tolesh-ai/tolesh` repository/namespace does not exist;
  (b) `barissencan.com/tolesh/` has not been deployed.
- **Every attribution destination that can be validated resolves (`200`)**:
  `isair/jarvis`, `isair`, `FatihMakes/Mark-LV`. No attribution link is broken.
- `LICENSES/` and `ATTRIBUTION.md` are referenced as **plain text, not URLs**,
  so no attribution hyperlink exists in the generated output. Both files exist
  locally.
- GitHub rate limiting was re-checked at verification time:
  `core limit/remaining/used = 60 53 7`, and
  `GET /repos/tolesh-ai/tolesh` returned `HTTP 404`. **Not rate limited** —
  remote verification succeeded, so the `404`s are real results, not a blocked
  probe.
- `$license` still returns `404` on **both** `main` and `master`. No successful
  HTTP verification exists; the link is **not fixed and not claimed fixed**.
- `site.json` was **not** modified during this audit. Its `M` state in
  `git status` **predates Priority 1**: it is the earlier rebrand change
  (`repository: isair/jarvis → tolesh-ai/tolesh`, plus `upstream_repository`),
  confirmed by `git diff -- website/site.json`. `source_branch` remains `main`.

## 7. Third-party assets (summary)

| Asset | Licence | Commercial note |
| --- | --- | --- |
| GSAP 3.13.0 (`web/vendor/gsap.min.js`) | Proprietary GSAP Standard "No Charge" License (Webflow) | Permitted for websites/apps; prohibits no-code animation-builder competitors and removal of notices. Header preserved. **Counsel review required** for commercial release. |
| Bodoni Moda woff2 | SIL OFL 1.1 | Permitted with notice; OFL text now bundled |
| Jost woff2 | SIL OFL 1.1 | Permitted with notice; OFL text now bundled |
| Python dependencies (`requirements.txt`) | Various, distributed with packages | **Not audited.** `pip-licenses` inventory outstanding. **PyQt6 is GPL/commercially dual-licensed** and needs counsel review before commercial distribution. |

Details: [`LICENSES/THIRD-PARTY-ASSETS.md`](LICENSES/THIRD-PARTY-ASSETS.md).

## 8. Determination and required action

**Determination (factual):** under the licence texts present in this
repository, commercial distribution of Tolesh is **not permitted**. The
restriction is not ambiguous: condition 2 forbids commercialisation, condition
3 extends the same terms to derivatives, and the COMMERCIAL USE section
requires a separate licence from the copyright holder.

**Required before any commercial distribution:**

1. Obtain a **separate written commercial licence** from Baris Sencan
   (contact in the licence: `baris@writeme.com`) covering Tolesh.
2. Retain all notices listed in [`ATTRIBUTION.md`](ATTRIBUTION.md) §5.
3. Have counsel review the combined work, including **PyQt6** dual licensing,
   the **GSAP** Standard License, and a full dependency licence inventory.
4. Only then update public licensing statements. **Until then, Tolesh must be
   described as non-commercial / personal use only.**

**This document does not grant, imply or claim any commercial rights.** It
records a requirement, as instructed.

## 9. Outstanding / not done

- **Public-site licence link is still broken (404)** — the unpublished
  `tolesh-ai/tolesh` repository. **Not fixed** and **not claimed fixed**: no
  successful HTTP verification exists for it. §6c re-verified **all 16** URLs
  in the rebuilt output: 7 `tolesh-ai/tolesh` links and 4 canonical
  `barissencan.com/tolesh/` links return `404`; all 5 external attribution /
  vocabulary links return `200`; GitHub was **not** rate limited
  (`remaining 53/60`). Requires the project owner to publish the repository
  (with the root `LICENSE`), or to supply the actual published destination, or
  to approve an in-site licence page — and, separately, to deploy the site.
  `site.json` was left untouched pending that decision.
- `site.json` `source_branch: main` vs local branch `master` — mismatch
  recorded, not changed.
- No legal opinion obtained; no lawyer engaged.
- No commercial licence negotiated or obtained (no contact made with the
  copyright holder).
- No `pip-licenses` audit of the Python dependency tree.
- No review of the GPL/commercial implications of PyQt6.
- No archived snapshot of the GSAP Standard License text in this repository
  (it is linked, not copied, because it is a web page subject to Webflow ToS).

## 10. Related documents

[`LICENSE`](LICENSE) · [`ATTRIBUTION.md`](ATTRIBUTION.md) ·
[`LICENSES/`](LICENSES/) · [`LICENSES/THIRD-PARTY-ASSETS.md`](LICENSES/THIRD-PARTY-ASSETS.md) ·
[`ARCHITECTURE.md`](ARCHITECTURE.md) · [`README.md`](README.md) ·
[`IMPLEMENTATION-REPORT.md`](IMPLEMENTATION-REPORT.md)
