"""Behavioural tests for the assistant-reply Markdown renderer.

The renderer lives in ``web/js/components.js`` and is the only place where
model output becomes HTML. That makes it the one place in the interface where a
hostile reply could turn into markup, so it is tested by actually executing it
in Node rather than by grepping the source.

The module is copied to a ``.mjs`` file first: the repository ships plain
``.js`` for the browser (no ``package.json`` with ``"type": "module"``), and
Node would otherwise parse it as CommonJS.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "web" / "js" / "components.js"

NODE = shutil.which("node")


pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


HARNESS = """
const m = await import(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
const out = {};
for (const [name, src] of Object.entries(cases)) out[name] = m.renderMarkdown(src);
process.stdout.write(JSON.stringify(out));
"""


def render(cases: dict[str, str]) -> dict[str, str]:
    """Run ``renderMarkdown`` in Node for every case and return the HTML."""
    tmp = COMPONENTS.with_suffix(".mjs")
    tmp.write_text(COMPONENTS.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        proc = subprocess.run(
            [NODE, "--input-type=module", "-e", HARNESS, tmp.as_uri(),
             json.dumps(cases)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    finally:
        tmp.unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def test_emphasis_and_inline_code_render():
    html = render({"x": "**bold** and *ital* and `code`"})["x"]
    assert "<strong>bold</strong>" in html
    assert "<em>ital</em>" in html
    assert "<code>code</code>" in html


def test_raw_html_in_a_reply_cannot_become_markup():
    html = render({"x": '<img src=x onerror="alert(1)">'})["x"]
    assert "<img" not in html
    assert "&lt;img" in html
    assert "onerror" in html  # shown as text, which is the point


def test_script_tag_is_escaped():
    html = render({"x": "<script>alert(1)</script>"})["x"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_javascript_urls_are_not_linked():
    html = render({"x": "[click](javascript:alert(1))"})["x"]
    assert "<a " not in html
    assert "javascript:alert(1)" in html


def test_https_links_open_outside_the_app():
    html = render({"x": "[docs](https://example.com/a)"})["x"]
    assert 'href="https://example.com/a"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'target="_blank"' in html


def test_fenced_code_is_escaped_not_executed():
    html = render({"x": "```py\nprint('<b>')\n```"})["x"]
    assert "<pre><code>" in html
    assert "&lt;b&gt;" in html
    assert "<b>" not in html


def test_unordered_list():
    html = render({"x": "- one\n- two"})["x"]
    assert html.count("<li>") == 2
    assert "<ul>" in html


def test_numbered_items_keep_their_numbers_across_a_code_block():
    """A code block between items ends the list; numbering must not restart."""
    html = render({"x": "1. one\n\n```\nx = 1\n```\n\n2. two\n\n```\ny = 2\n```\n\n3. three"})["x"]
    assert '<ol start="2">' in html
    assert '<ol start="3">' in html
    assert html.count("<li>") == 3


def test_pipe_table():
    html = render({"x": "| a | b |\n|---|---|\n| 1 | 2 |"})["x"]
    assert "<table>" in html
    assert "<th>a</th>" in html
    assert "<td>2</td>" in html


def test_headings_map_to_the_document_outline_not_h1():
    html = render({"x": "## Section"})["x"]
    assert "<h4>Section</h4>" in html
    assert "<h1>" not in html


def test_blank_lines_separate_paragraphs():
    html = render({"x": "first\n\nsecond"})["x"]
    assert html.count("<p>") == 2


def test_plain_text_still_renders_as_one_paragraph():
    html = render({"x": "How far, wetin dey?"})["x"]
    assert html == "<p>How far, wetin dey?</p>"
