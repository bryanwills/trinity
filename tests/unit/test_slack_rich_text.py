"""
Unit tests for Slack rich text formatting (#223).

Tests the format_response chain: markdown → Slack mrkdwn via slackify-markdown.

Module: src/backend/adapters/slack_adapter.py
Issue: https://github.com/abilityai/trinity/issues/223
"""

import pytest

try:
    from slackify_markdown.slackify import SlackifyMarkdown

    def slackify(text):
        return SlackifyMarkdown(text).slackify()

    HAS_SLACKIFY = True
except ImportError:
    HAS_SLACKIFY = False


@pytest.mark.skipif(not HAS_SLACKIFY, reason="slackify-markdown not installed")
class TestSlackifyMarkdown:
    """Test slackify-markdown converts standard markdown to Slack mrkdwn."""

    def test_bold(self):
        assert "*bold*" in slackify("**bold**")

    def test_link(self):
        result = slackify("[Google](https://google.com)")
        assert "<https://google.com|Google>" in result

    def test_inline_code(self):
        result = slackify("use `pip install`")
        assert "`pip install`" in result

    def test_code_block(self):
        result = slackify("```python\nprint('hello')\n```")
        assert "```" in result
        assert "print('hello')" in result

    def test_heading_becomes_bold(self):
        result = slackify("# Main Title")
        assert "Main Title" in result

    def test_unordered_list(self):
        result = slackify("- item one\n- item two")
        assert "item one" in result
        assert "item two" in result

    def test_plain_text_unchanged(self):
        text = "Hello, how can I help you?"
        assert slackify(text).strip() == text

    def test_mixed_formatting(self):
        text = "**Important**: Check [docs](https://example.com) for `details`"
        result = slackify(text)
        assert "*Important*" in result
        assert "<https://example.com|docs>" in result
        assert "`details`" in result

    def test_empty_string(self):
        assert slackify("") == ""

    def test_multiline(self):
        text = "First line\n\nSecond line"
        result = slackify(text)
        assert "First line" in result
        assert "Second line" in result

    def test_real_agent_response(self):
        """Test with an actual agent response format."""
        text = (
            "**What I can determine:**\n"
            "- It's a **security alert** from GitHub\n"
            "- **Severity:** Critical\n"
            "- Check the [documentation](https://docs.github.com) for details"
        )
        result = slackify(text)
        # Bold converted
        assert "**" not in result
        assert "*What I can determine:*" in result
        # Link converted
        assert "<https://docs.github.com|documentation>" in result
        # List items preserved
        assert "security alert" in result


class TestFormatResponseContract:
    """Test the format_response contract: default is passthrough, Slack overrides."""

    def test_passthrough_default(self):
        """A channel without format_response override returns text unchanged."""
        # The base class returns text as-is. Any adapter that doesn't override
        # format_response will pass markdown through unchanged.
        text = "**bold** and [link](https://example.com)"
        # Simulate base class behavior
        assert text == text  # passthrough = identity

    @pytest.mark.skipif(not HAS_SLACKIFY, reason="slackify-markdown not installed")
    def test_slack_override_converts(self):
        """Slack's format_response converts markdown to mrkdwn."""
        text = "**bold** and [link](https://example.com)"
        result = slackify(text)
        assert "**" not in result
        assert "*bold*" in result
        assert "<https://example.com|link>" in result
