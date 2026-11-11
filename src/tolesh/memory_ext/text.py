"""Text handling for memory: normalisation, tokens, light stemming, similarity.

Deliberately dependency-free and deterministic. The retrieval this feeds is
lexical plus character n-grams, not a neural embedding model: it matches
"preferred" against "prefer" and "FastAPI" against "fast api", and it will not
match "editor" against "IDE". Everything here is inspectable, which matters more
for a memory store the user is supposed to audit than a similarity score nobody
can explain.

Language-agnostic: ``\\w`` in Python 3 is Unicode-aware, so Yoruba diacritics,
CJK, Cyrillic and Arabic tokenise without a special case. Character n-grams are
what carry the CJK case, where there are no spaces to split on.
"""

from __future__ import annotations

import math
import re
from typing import Iterable

_WORD = re.compile(r"[^\W_]+", re.UNICODE)

# Function words across the languages Tolesh actually meets. Removing them is
# what stops "what is my preferred stack" from matching every memory that
# happens to contain "is" or "my".
STOPWORDS: frozenset[str] = frozenset("""
a an the and or but if then else of in on at to for with by from about into over
under again further once here there all any both each few more most other some
such no nor not only own same so than too very can will just should now
i me my myself we our ours you your yours he him his she her hers it its they
them their theirs this that these those am is are was were be been being do
does did doing have has had having would could might must shall may
what which who whom whose when where why how
tell show give find know think want need get got make made let lets
please thanks thank ok okay yes yeah nope
sir ma am
""".split())

# Concept expansion, not a thesaurus. Each group collapses the vocabulary people
# actually use for the things Tolesh is asked to remember, so a question about
# "my tech stack" reaches a memory phrased "I prefer Python". Anything outside
# these groups relies on the words themselves — the honest limit of a lexical
# retriever, stated in docs/MEMORY.md.
#
# The technology group is long on purpose: without it "what stack do I use?"
# shares no word at all with "I prefer Python and FastAPI", and the memory that
# answers the question is never retrieved. A concept collapses the specific
# name, so the character n-grams in `similarity` carry the precision.
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("prefer", "preference", "favourite", "favorite", "like", "love", "rather", "choice"),
    ("tech", "technology", "stack", "tooling", "framework", "library",
     "python", "fastapi", "flask", "django", "node", "nodejs", "react", "vue",
     "svelte", "nextjs", "tailwind", "typescript", "javascript", "rust",
     "golang", "java", "kotlin", "swift", "php", "ruby", "rails", "graphql",
     "docker", "kubernetes", "aws", "gcp", "azure", "linux", "windows", "macos"),
    ("name", "called", "call"),
    ("work", "job", "role", "profession", "career", "occupation",
     "engineer", "developer", "designer", "founder", "manager", "student",
     "freelancer", "researcher", "teacher", "doctor"),
    ("live", "lives", "living", "location", "city", "based", "country", "reside",
     "lagos", "abuja", "london", "berlin", "nairobi", "accra", "nigeria"),
    ("project", "building", "build", "working", "product", "app", "codebase"),
    ("goal", "aim", "objective", "target", "plan", "roadmap"),
    ("decide", "decided", "decision", "chose", "choose", "chosen", "settled"),
    ("style", "tone", "voice", "register", "communicate", "reply", "answer"),
    ("timezone", "time", "zone", "clock", "utc"),
    ("language", "tongue", "speak", "speaks"),
    ("editor", "ide", "vim", "emacs", "vscode"),
    ("database", "db", "sqlite", "postgres", "postgresql", "mysql", "mongo",
     "mongodb", "redis"),
    ("food", "eat", "meal", "jollof", "rice", "cook", "cooking"),
)

_SYNONYM: dict[str, str] = {}
for _group in _SYNONYM_GROUPS:
    for _word in _group:
        _SYNONYM[_word] = _group[0]

# Suffixes stripped by the light stemmer, longest first so "ations" wins over
# "s". Applied only when at least three characters survive, which is what keeps
# "news" from becoming "new" and "was" from becoming "wa".
_SUFFIXES = ("ations", "ation", "ingly", "edly", "ing", "ies", "ied", "ed",
             "es", "ly", "er", "est", "s")


def normalise(text: str) -> str:
    """Lowercase and collapse whitespace, keeping punctuation out of the way."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def stem(word: str) -> str:
    """A deliberately small suffix stripper.

    Not a Porter stemmer: it has no notion of English morphology, it just
    removes endings that otherwise split "preferred" from "prefer". Anything it
    gets wrong only costs a little recall, never a wrong memory, because the
    final ranking also uses character n-grams.
    """
    w = word
    for suffix in _SUFFIXES:
        if len(w) - len(suffix) >= 3 and w.endswith(suffix):
            w = w[: -len(suffix)]
            break
    # "preferr" -> "prefer": undo the doubled consonant English adds before -ed.
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
        w = w[:-1]
    return w


def tokens(text: str, *, expand: bool = True) -> list[str]:
    """Content words, stemmed and (optionally) mapped to their concept.

    The concept lookup happens on the *raw* word, before stemming. Stemming
    first turned "Lagos" into "lago" and "engineer" into "engine", which then
    matched nothing in the concept table — the lookup silently did nothing for
    exactly the words it was added for.
    """
    out: list[str] = []
    for raw in _WORD.findall(normalise(text)):
        if len(raw) < 2 or raw in STOPWORDS or raw.isdigit():
            continue
        if expand:
            concept = _SYNONYM.get(raw)
            if concept:
                out.append(concept)
                continue
        word = stem(raw)
        if word in STOPWORDS or len(word) < 2:
            continue
        if expand:
            word = _SYNONYM.get(word, word)
        out.append(word)
    return out


def token_set(text: str, *, expand: bool = True) -> set[str]:
    return set(tokens(text, expand=expand))


def trigrams(text: str) -> set[str]:
    """Character 3-grams over the whole normalised string.

    This is what lets "FastAPI" match "fast api" and gives CJK text, which has
    no word boundaries, something to compare.
    """
    compact = re.sub(r"[^\w]+", " ", normalise(text), flags=re.UNICODE).strip()
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[i:i + 3] for i in range(len(compact) - 2)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(needle: set[str], haystack: set[str]) -> float:
    """How much of *needle* is covered by *haystack*.

    Asymmetric on purpose: a short question ("my stack?") should score well
    against a long memory, where symmetric Jaccard would punish the length
    difference.
    """
    if not needle:
        return 0.0
    return len(needle & haystack) / len(needle)


def similarity(query: str, content: str) -> float:
    """Blend concept overlap, question coverage and character n-gram overlap.

    Concept overlap carries recall ("stack" reaches "Python"), coverage keeps a
    short question from being punished for brevity, and n-grams carry precision
    (they are computed on the raw text, so "Lagos" and "London" do not blur
    together just because both are locations).

    The weights were set against the retrieval corpus in
    ``tests/test_memory_retrieval.py``: raising the n-gram share starts dropping
    real matches, raising the concept share starts returning everything.
    """
    q_con, c_con = token_set(query), token_set(content)
    q_raw, c_raw = token_set(query, expand=False), token_set(content, expand=False)
    concept = 0.5 * jaccard(q_con, c_con) + 0.5 * containment(q_con, c_con)
    raw = jaccard(q_raw, c_raw)
    grams = jaccard(trigrams(query), trigrams(content))
    return 0.40 * concept + 0.20 * raw + 0.40 * grams


def recency_weight(updated_at: float, now: float, *, half_life_days: float = 45.0) -> float:
    """Exponential decay in [0, 1]; a memory loses half its recency score every
    ``half_life_days``. Fresh corrections therefore outrank the fact they
    replaced without deleting it."""
    if not updated_at:
        return 0.0
    age_days = max(0.0, (now - updated_at) / 86400.0)
    return math.pow(0.5, age_days / half_life_days)


def hash_vector(text: str, dims: int = 256) -> dict[int, float]:
    """Signed hashing-trick TF vector, L2-normalised.

    Stored per memory so a query can be compared with a dot product. It is a
    real vector index — it just isn't a learned embedding, and calling it
    "semantic" would be a lie. Used as a tie-breaker behind the lexical score.
    """
    counts: dict[int, float] = {}
    for gram in trigrams(text):
        h = hash(gram)
        index = h % dims
        sign = 1.0 if (h >> 8) & 1 else -1.0
        counts[index] = counts.get(index, 0.0) + sign
    norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
    return {k: v / norm for k, v in counts.items() if abs(v) > 1e-9}


def cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


def keywords(text: str, limit: int = 8) -> list[str]:
    """The words worth showing as a memory's "why this matched" hint."""
    seen: list[str] = []
    for word in tokens(text):
        if word not in seen:
            seen.append(word)
        if len(seen) >= limit:
            break
    return seen


def first_sentence(text: str, *, limit: int = 400) -> str:
    """One sentence, trimmed. Memory entries are facts, not transcripts."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    match = re.match(r"(.{10,%d}?[.!?])(\s|$)" % limit, cleaned)
    if match:
        return match.group(1).strip()
    return cleaned[:limit].strip()


def dedupe_lines(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = normalise(line)
        if key and key not in seen:
            seen.add(key)
            out.append(line)
    return out
