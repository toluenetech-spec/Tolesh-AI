# Tolesh — Attribution and Licence Notices

> **This file is part of the licence compliance record.** Copyright and
> attribution notices below are preserved verbatim. Do not remove or alter them.
> Tolesh is a derivative work; its governing licence is the upstream
> *Jarvis AI Assistant License*, reproduced verbatim in [`LICENSE`](LICENSE) and
> [`LICENSES/isair-jarvis-LICENSE.txt`](LICENSES/isair-jarvis-LICENSE.txt).

## 1. Governing licence — READ THIS FIRST

Tolesh was built by copying the [`isair/jarvis`](https://github.com/isair/jarvis)
source tree, rebranding it, and adding new modules. It is therefore a
**derivative work** of that project.

The upstream licence states, verbatim:

- *"Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software ... to use, copy, modify, merge, publish, and distribute the
  Software for **non-commercial purposes**"*
- Condition 1: *"The above copyright notice and this permission notice appear in
  all copies."*
- Condition 2: *"You do not sell, rent, lease, or otherwise commercialise the
  Software."*
- Condition 3: *"Any derivative works are also licensed under these same terms."*
- *"Commercial use of the Software requires a separate commercial license from
  the copyright holder."*
- Commercial-licence contact: **baris@writeme.com**

**Conclusion (factual, not legal advice):** the current licence does **not**
permit commercial distribution of Tolesh as-is. Commercial distribution requires
a separate written commercial licence from the copyright holder,
**Baris Sencan**. This project makes **no claim** of being commercially
distributable without that clearance. See
[`LICENSING-ASSESSMENT.md`](LICENSING-ASSESSMENT.md).

Upstream copyright notice, preserved verbatim:

```
Jarvis AI Assistant License

Copyright (c) 2025 Baris Sencan
```

## 2. Upstream projects

| Project | Copyright holder | Licence | Role in Tolesh | Notice preserved |
| --- | --- | --- | --- | --- |
| [`isair/jarvis`](https://github.com/isair/jarvis) | Copyright (c) 2025 Baris Sencan | Jarvis AI Assistant License — **non-commercial**, derivatives must use the same terms, commercial use requires a separate licence | **Foundation**: source copied verbatim, 479 files rebranded, extended with new modules | [`LICENSE`](LICENSE), [`LICENSES/isair-jarvis-LICENSE.txt`](LICENSES/isair-jarvis-LICENSE.txt), [`docs/upstream/`](docs/upstream/) |
| [`FatihMakes/Mark-LV`](https://github.com/FatihMakes/Mark-LV) | Copyright (c) 2026 FatihMakes | **CC BY-NC 4.0** — non-commercial | Inspected for concepts only; **no code copied**. The confirmation-gate *idea* was re-implemented from scratch in `permissions/policy.py` and `permissions/gate.py` | [`LICENSES/Mark-LV-LICENSE.txt`](LICENSES/Mark-LV-LICENSE.txt) |
| [`jev-chat/jev-chat-jarvis`](https://github.com/jev-chat/jev-chat-jarvis) | Copyright (c) 2026 Finderchangchang and the jev-chat contributors | MIT — permits commercial use, requires the LICENSE + NOTICE and source credit | Inspected for provider ideas; **not used as a code base** | [`LICENSES/jev-chat-jarvis-LICENSE.txt`](LICENSES/jev-chat-jarvis-LICENSE.txt), [`LICENSES/jev-chat-jarvis-NOTICE.txt`](LICENSES/jev-chat-jarvis-NOTICE.txt) |

The jev-chat-jarvis NOTICE requests that derivatives credit the source, e.g.
*"Based on Jev Chat Assistant (https://github.com/jev-chat/jev-chat-jarvis)"*.
Tolesh did not copy its code, but the NOTICE and licence are retained in full
for completeness and because the repository was inspected.

Archived upstream documentation (README / AGENTS / EVALS) is kept unchanged in
[`docs/upstream/`](docs/upstream/).

## 3. Third-party assets

Full details and the audit checklist are in
[`LICENSES/THIRD-PARTY-ASSETS.md`](LICENSES/THIRD-PARTY-ASSETS.md).

| Asset | Version / source | Copyright | Licence | Notice status |
| --- | --- | --- | --- | --- |
| GSAP | 3.13.0, bundled as [`web/vendor/gsap.min.js`](web/vendor/gsap.min.js) | Copyright 2025, GreenSock / Webflow | GSAP Standard "No Charge" License — <https://gsap.com/standard-license/> | Proprietary header preserved **verbatim** in the bundled file |
| Bodoni Moda (woff2) | Google Fonts | Copyright 2020 The Bodoni Moda Project Authors | SIL Open Font License 1.1 | [`LICENSES/OFL-Bodoni-Moda.txt`](LICENSES/OFL-Bodoni-Moda.txt) |
| Jost (woff2) | Google Fonts | Copyright 2020 The Jost Project Authors | SIL Open Font License 1.1 | [`LICENSES/OFL-Jost.txt`](LICENSES/OFL-Jost.txt) |

GSAP's licence prohibits removing or altering proprietary notices and prohibits
use in no-code visual-animation-building tools that compete with Webflow.
Tolesh uses GSAP for its own interface animation and keeps the header intact.
The licence text itself is hosted by Webflow and is linked rather than
reproduced here; it is also subject to Webflow's Terms of Service.

## 4. New Tolesh-authored code

Modules added under `src/tolesh/ai/`, `src/tolesh/conversation/`,
`src/tolesh/memory_ext/`, `src/tolesh/permissions/`, `src/tolesh/persona/`,
`src/tolesh/server/`, `src/tolesh/tools_ext/`, plus `web/`, the root documents
and the new tests, were authored for Tolesh. **They do not change the licensing
status of the whole work**: because the repository is a derivative of
`isair/jarvis`, upstream condition 3 applies to the combined work. Any
commercial release must be cleared with the copyright holder and reviewed by
counsel. No separate licence grant is made here.

## 5. What must not be removed

- The root [`LICENSE`](LICENSE) (verbatim upstream text, including
  `Copyright (c) 2025 Baris Sencan`).
- Every file in [`LICENSES/`](LICENSES/).
- [`docs/upstream/`](docs/upstream/) archived upstream documentation.
- The upstream attribution in [`website/privacy.html`](website/privacy.html)
  naming Baris Sencan (isair), FatihMakes and the two upstream projects.
- The GSAP proprietary header in `web/vendor/gsap.min.js`.
- This file, which `website/privacy.html` links to.
