"""Tests for the unified persona system prompt.

The persona should match the user's configured wake word so renaming the
wake word to e.g. "Friday" produces a butler named Friday, not one still
hardcoded to Tolesh.
"""

from tolesh.system_prompt import build_system_prompt


class TestBuildSystemPrompt:
    def test_default_name_is_tolesh(self):
        prompt = build_system_prompt()
        assert "named Tolesh" in prompt

    def test_custom_name_replaces_tolesh(self):
        prompt = build_system_prompt("Friday")
        assert "named Friday" in prompt
        assert "named Tolesh" not in prompt

    def test_lowercase_wake_word_is_capitalised(self):
        prompt = build_system_prompt("friday".capitalize())
        assert "named Friday" in prompt

    def test_blank_name_falls_back_to_tolesh(self):
        assert "named Tolesh" in build_system_prompt("")
        assert "named Tolesh" in build_system_prompt("   ")
        assert "named Tolesh" in build_system_prompt(None)  # type: ignore[arg-type]
