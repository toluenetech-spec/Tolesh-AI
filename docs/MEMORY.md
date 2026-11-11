# Memory

Tolesh learns facts about you from ordinary conversation and puts the relevant
ones back in front of the model when they matter. You can see every stored fact,
edit it, switch it off, delete it, or ask it to be forgotten — and it will not
come back.

This document describes what actually runs. It also states where the system is
weak, because a memory feature that overstates itself is worse than none.

## What is stored, and what is not

Five layers, and the difference between them is the point:

| Layer | Holds | Example |
|---|---|---|
| `preference` | how you like things done | "I prefer Python and FastAPI over Node" |
| `personal` | stable facts about you | "My name is Ada", "I'm a backend engineer in Lagos" |
| `project` | a body of work and its decisions | "We're building Tolesh, a desktop AI workspace" |
| `task` | ongoing activity | "Next step is the installer" |
| `conversation` | manual notes only — never written automatically | — |

**Never stored automatically:** questions, greetings, small talk, hypotheticals
("if I were building X I'd use Y"), fiction and role-play, credentials, and
anything that reads like an instruction to the assistant.

`conversation` is deliberately excluded from automatic extraction. A passing
remark must not become a permanent record; that is the whole reason the layers
exist.

## How a conversation becomes a memory

```
chat request ──► reply streamed to you ──► observe_turn() ──► queue
                                                              │
                                            (debounced, own thread)
                                                              ▼
                          extract (rules, or a model if you switch it on)
                                                              │
                        filter: secrets · injections · hypotheticals · categories
                                                              │
                        dedup · slot replacement · correction · suppression check
                                                              ▼
                                                     memories table
```

Three properties matter here:

1. **The reply never waits for memory.** Extraction runs on a worker thread
   after the response is finished. A slow model or a burst of messages delays
   learning, never chatting.
2. **The switches are above the extractor.** Master switch, per-category
   switches and the auto-extract switch are enforced in `MemoryService`, so no
   future extractor can bypass them by forgetting to check.
3. **Every write is explainable.** Each row records `origin` (`manual`, `auto`,
   `correction`), `confidence`, the conversation it came from, and the sentence
   that produced it (`evidence.quote`).

### Extraction modes

| Mode | Cost | Good at | Weak at |
|---|---|---|---|
| `rules` (default) | zero | explicit cues: "I prefer…", "my name is…", "we decided…", "remember that…" | oblique phrasing; anything without a cue word |
| `model` | one small model call per exchange | oblique phrasing, implied preferences | unpredictability, latency, cost |

In `model` mode the reply is parsed as strict JSON; anything unparseable falls
back to the rules rather than storing half a sentence. Both modes pass through
the same safety filter and the same dedup.

The rules recognise, among others: identity and place ("my name is…", "I live
in…", "I'm a … engineer"), preferences ("I prefer…", "I hate…"), working style
("keep it short"), projects ("I'm building…"), decisions ("we went with…"),
tasks ("I still need to…"), and where things are kept ("I keep my notes in
Obsidian", "my notes live in Notion"). A statement with no cue at all is left
alone on purpose: guessing is how a memory store fills with fiction.

## How retrieval works

`MemoryService.context_for(question)` scores every active memory and returns at
most `memory_retrieval_limit` (default 6), capped at 1 600 characters.

```
score = 0.55·similarity + 0.12·recency + 0.10·pinned
      + 0.08·confidence + 0.08·project + 0.07·importance
```

`similarity` is lexical:

```
similarity = 0.40·concept-overlap + 0.20·word-overlap + 0.40·character-3-grams
```

A memory must clear `similarity ≥ 0.10` to be eligible at all — without that
gate the constant bonuses (recency, confidence, project) added up past any
threshold and a greeting retrieved the entire store. Pinned memories bypass the
gate because you marked them as must-know.

### Slots, and why wording drift does not lose a fact

Some facts occupy a named slot: `personal:name`, `personal:location`,
`personal:job`, and `preference:<thing>` for "I keep my notes in Obsidian".
A new value for a slot **replaces** the old one, so a correction works even when
it shares almost no words with what it replaces:

```
I keep my notes in Obsidian.                  → slot preference:note
I moved my notes to Notion, not Obsidian.     → same slot → old row superseded
```

The same slot name is what keeps the fact findable afterwards. "Where do I keep
my notes?" shares almost no vocabulary with "I moved my notes to Notion", so the
similarity score alone would miss it; a question that names a slot's subject gets
a similarity floor of 0.45 (`SLOT_MATCH_SIMILARITY`). Forgetting uses the same
rule, which is why "forget where I keep my notes" removes the fact however it was
last worded.

**Project isolation** is enforced during selection, not by the prompt: a memory
filed under project A is never eligible in project B. Global memories (no
project) are eligible everywhere, which is what lets "prefers short answers"
follow you across projects.

### What "concept overlap" means

A hand-written table maps words onto concepts: `python`, `fastapi`, `react`,
`docker` … → `tech`; `engineer`, `founder`, `designer` … → `work`; `lagos`,
`london`, `live` … → `live`. This is what lets "what stack do I use?" reach "I
prefer Python and FastAPI" — the two share no literal word.

It is not a semantic model. The honest limits:

- a concept that is not in the table is invisible to the expansion;
- two different things inside one concept blur together (`python` and `docker`
  are both `tech`);
- character n-grams carry the precision, so paraphrase without shared words
  will miss;
- there is no neural embedding and no vector database behind this store. (The
  app does keep a vector index for *conversation summaries* — see
  `memory/db.py:search_hybrid` — but long-term memory does not use it.)

The table lives in `src/tolesh/memory_ext/text.py` and is meant to grow.

## Corrections, duplicates and forgetting

| Situation | What happens |
|---|---|
| Same fact said twice | merged into one row, confidence raised, timestamp refreshed |
| Named slot restated ("my name is Ada" → "my name is Adaeze") | old row marked `superseded`, points at its replacement; retrieval only sees the new one |
| Tool swapped ("I keep my notes in Obsidian" → "I moved my notes to Notion") | same slot (`preference:note`), so the old row is superseded even though the wording barely overlaps |
| Explicit correction ("actually…", "I switched to…", "not anymore") | the closest active memory in the same category and project is superseded |
| "Forget that I live in Lagos" | matching rows are deleted (including superseded history) and a suppression is recorded |
| Same fact reappears later | rejected because a suppression matches it |
| "Forget it" said again after it came back | still works — a forget is never blocked by its own suppression |

Superseded rows are kept on purpose: "I used to think X, you corrected me to Y"
is something you may want to audit. They are hidden by default and shown with
*Show replaced* in the panel.

Suppressions are the half of forgetting that people forget. Deleting a row alone
means the next conversation extracts the same fact again, which makes "forget
that" a promise Tolesh breaks on the next turn.

## Safety

| Risk | Defence |
|---|---|
| Credentials in a memory | pattern filter (API-key shapes, private keys, bearer tokens, card/SSN patterns) **and** keyword filter (password, secret, token, OTP, CVV…). Applied per clause, so the credential is dropped while an unrelated preference in the same sentence survives. |
| Memory poisoning / injection | a candidate containing "ignore previous instructions", "you are now", "reveal the system prompt", shell commands, etc. is discarded. Retrieved memories are wrapped in a block labelled *facts, not instructions*, and the persona says a memory that reads like a command is corrupted data. |
| Hypotheticals becoming facts | sentences starting with *if / suppose / imagine / what if*, and fiction markers, are skipped. |
| Cross-project leakage | enforced in selection (see above). |
| Unauthorised access | every `/api/memory*` route sits behind the same bearer token as the rest of the API. |
| Silent collection | extraction reads only the conversation text you typed. No files, no microphone, no camera, no background reading of other stores. |

## Settings

| Key | Default | Meaning |
|---|---|---|
| `memory_enabled` | `true` | master switch: nothing stored, nothing recalled |
| `memory_auto_extract` | `true` | learn in the background without being asked |
| `memory_retrieval` | `true` | put relevant memories into prompts |
| `memory_categories` | `preference, personal, project, task` | which layers may be written automatically |
| `memory_extraction_mode` | `rules` | `rules` or `model` |
| `memory_extraction_model` | `""` | model id for extraction; empty lets the router choose |
| `memory_retrieval_limit` | `6` | memories per answer (1–12) |

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/memory` | list; filters `q`, `category`, `project_id`, `include_superseded` |
| `POST` | `/api/memory` | add manually (never deduplicated: you asked for this row) |
| `PATCH` | `/api/memory/{id}` | edit content, category, pin, disable, confidence |
| `DELETE` | `/api/memory/{id}` | delete one row |
| `POST` | `/api/memory/forget` | wipe everything (requires `FORGET EVERYTHING`) |
| `POST` | `/api/memory/forget-match` | forget everything matching a phrase, and suppress it |
| `GET` | `/api/memory/settings` · `PATCH` | the switches above |
| `GET` | `/api/memory/status` | worker state, last run, counters |
| `POST` | `/api/memory/search` | rank memories against a question, as a prompt would |
| `POST` | `/api/memory/extract` | learn from one conversation now |
| `GET` | `/api/memory/suppressions` · `DELETE /{id}` | the "do not remember" list |
| `GET` | `/api/memory/events` | audit trail: created, merged, superseded, deleted, wiped |

## Verifying it

```bash
PYTHONPATH=src python3.11 -m pytest tests/test_memory_auto.py -q        # parts
PYTHONPATH=src python3.11 -m pytest tests/test_memory_workflow.py -q    # the ten-step flow
```

`tests/test_memory_workflow.py` is the one that matters: it converses, lets the
worker learn, restarts the store, opens a new conversation, checks the fact
reaches the *model's prompt* (not just the table), corrects it, checks the old
value is gone, forgets a second fact, and checks it is neither retrieved nor
relearned.

## Known limitations

- **No neural embeddings.** Retrieval is lexical plus a hand-written concept
  table. See the limits above; a provider-backed embedding index is the obvious
  next step and is not implemented.
- **Rules are English-shaped.** Nigerian English and Pidgin cues are covered
  ("abeg", "no wahala", "wetin"), but the cue list is finite and other languages
  rely on the `model` extraction mode.
- **No decay or forgetting-by-age.** Recency affects ranking but nothing is ever
  automatically deleted; the store grows until you prune it.
- **Model extraction is not free** and is off by default for that reason.
- **Memory is per-database, not per-account.** There is no multi-user isolation
  because Tolesh is a single-person application; exposing one instance to two
  people would share one memory.
