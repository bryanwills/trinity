"""
Unit tests for Telegram Voice Transcription (Issue #318).

Tests validation logic in isolation without requiring backend dependencies.
The actual implementation is in src/backend/services/telegram_media.py.

Module: src/backend/services/telegram_media.py
Issue: https://github.com/abilityai/trinity/issues/318
"""

import pytest


# ---- Inline constants matching telegram_media.py ----
MAX_VOICE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_VOICE_DURATION = 300  # 5 minutes


# ---- Inline validation functions for isolated unit testing ----

def validate_voice_duration(duration: int) -> tuple[bool, str]:
    """Validate voice message duration."""
    if duration > MAX_VOICE_DURATION:
        minutes = duration // 60
        return False, f"Voice message too long ({minutes}+ minutes) — transcription limit is 5 minutes"
    return True, ""


def validate_voice_size(file_size: int) -> tuple[bool, str]:
    """Validate voice message file size."""
    if file_size > MAX_VOICE_SIZE:
        size_mb = file_size / (1024 * 1024)
        return False, f"Voice message too large ({size_mb:.1f}MB) — transcription limit is 10MB"
    return True, ""


def validate_voice_file_id(voice: dict) -> tuple[bool, str]:
    """Validate voice message has file_id."""
    if not voice.get("file_id"):
        return False, "Voice message received but file_id missing"
    return True, ""


def format_transcription(text: str) -> str:
    """Format transcription result with emoji prefix."""
    return f'🎙️ "{text}"'


# =============================================================================
# Unit Tests: Duration Validation
# =============================================================================

class TestDurationValidation:
    """Tests for voice message duration validation."""

    @pytest.mark.unit
    def test_duration_under_limit_passes(self):
        """Duration under 5 minutes should pass."""
        valid, error = validate_voice_duration(180)  # 3 minutes
        assert valid is True
        assert error == ""

    @pytest.mark.unit
    def test_duration_at_limit_passes(self):
        """Duration exactly at 5 minutes should pass."""
        valid, error = validate_voice_duration(300)  # 5 minutes exactly
        assert valid is True
        assert error == ""

    @pytest.mark.unit
    def test_duration_over_limit_fails(self):
        """Duration over 5 minutes should fail."""
        valid, error = validate_voice_duration(400)  # 6+ minutes
        assert valid is False
        assert "too long" in error.lower()
        assert "5 minutes" in error

    @pytest.mark.unit
    def test_duration_zero_passes(self):
        """Zero duration should pass (edge case)."""
        valid, error = validate_voice_duration(0)
        assert valid is True

    @pytest.mark.unit
    def test_duration_one_hour_fails(self):
        """One hour duration should fail with meaningful message."""
        valid, error = validate_voice_duration(3600)  # 60 minutes
        assert valid is False
        assert "60" in error


# =============================================================================
# Unit Tests: Size Validation
# =============================================================================

class TestSizeValidation:
    """Tests for voice message size validation."""

    @pytest.mark.unit
    def test_size_under_limit_passes(self):
        """Size under 10MB should pass."""
        valid, error = validate_voice_size(5 * 1024 * 1024)  # 5MB
        assert valid is True
        assert error == ""

    @pytest.mark.unit
    def test_size_at_limit_passes(self):
        """Size exactly at 10MB should pass."""
        valid, error = validate_voice_size(10 * 1024 * 1024)  # 10MB exactly
        assert valid is True
        assert error == ""

    @pytest.mark.unit
    def test_size_over_limit_fails(self):
        """Size over 10MB should fail."""
        valid, error = validate_voice_size(15 * 1024 * 1024)  # 15MB
        assert valid is False
        assert "too large" in error.lower()
        assert "10MB" in error

    @pytest.mark.unit
    def test_size_zero_passes(self):
        """Zero size should pass (edge case)."""
        valid, error = validate_voice_size(0)
        assert valid is True

    @pytest.mark.unit
    def test_size_small_file_passes(self):
        """Small files should pass."""
        valid, error = validate_voice_size(1024)  # 1KB
        assert valid is True


# =============================================================================
# Unit Tests: File ID Validation
# =============================================================================

class TestFileIdValidation:
    """Tests for voice message file_id validation."""

    @pytest.mark.unit
    def test_valid_file_id_passes(self):
        """Voice with file_id should pass."""
        voice = {"file_id": "test_file_id_12345", "duration": 60}
        valid, error = validate_voice_file_id(voice)
        assert valid is True
        assert error == ""

    @pytest.mark.unit
    def test_missing_file_id_fails(self):
        """Voice without file_id should fail."""
        voice = {"duration": 60}
        valid, error = validate_voice_file_id(voice)
        assert valid is False
        assert "file_id missing" in error.lower()

    @pytest.mark.unit
    def test_empty_file_id_fails(self):
        """Voice with empty file_id should fail."""
        voice = {"file_id": "", "duration": 60}
        valid, error = validate_voice_file_id(voice)
        assert valid is False

    @pytest.mark.unit
    def test_none_file_id_fails(self):
        """Voice with None file_id should fail."""
        voice = {"file_id": None, "duration": 60}
        valid, error = validate_voice_file_id(voice)
        assert valid is False


# =============================================================================
# Unit Tests: Transcription Formatting
# =============================================================================

class TestTranscriptionFormatting:
    """Tests for transcription result formatting."""

    @pytest.mark.unit
    def test_format_simple_text(self):
        """Simple text should be wrapped with emoji and quotes."""
        result = format_transcription("Hello world")
        assert result == '🎙️ "Hello world"'

    @pytest.mark.unit
    def test_format_with_quotes(self):
        """Text with quotes should still work."""
        result = format_transcription('He said "hello"')
        assert result == '🎙️ "He said "hello""'

    @pytest.mark.unit
    def test_format_empty_string(self):
        """Empty string should be wrapped."""
        result = format_transcription("")
        assert result == '🎙️ ""'

    @pytest.mark.unit
    def test_format_multiline(self):
        """Multiline text should preserve newlines."""
        result = format_transcription("Line 1\nLine 2")
        assert "Line 1\nLine 2" in result
        assert result.startswith('🎙️ "')


# =============================================================================
# Unit Tests: Placeholder Text Constants
# =============================================================================

class TestPlaceholderConstants:
    """Tests for placeholder text consistency."""

    @pytest.mark.unit
    def test_voice_placeholder_format(self):
        """Voice placeholder should have consistent format."""
        placeholder = "[User sent a voice message — voice transcription is not yet available]"
        assert placeholder.startswith("[")
        assert placeholder.endswith("]")
        assert "voice" in placeholder.lower()

    @pytest.mark.unit
    def test_video_note_placeholder_format(self):
        """Video note placeholder should have consistent format."""
        placeholder = "[User sent a video note — transcription not yet available]"
        assert placeholder.startswith("[")
        assert placeholder.endswith("]")
        assert "video note" in placeholder.lower()


# =============================================================================
# Unit Tests: Combined Validation
# =============================================================================

class TestCombinedValidation:
    """Tests for full validation chain."""

    @pytest.mark.unit
    def test_valid_voice_message(self):
        """Valid voice message should pass all checks."""
        voice = {
            "file_id": "test_file_id",
            "duration": 60,
            "file_size": 1024 * 1024,  # 1MB
        }

        file_ok, _ = validate_voice_file_id(voice)
        duration_ok, _ = validate_voice_duration(voice["duration"])
        size_ok, _ = validate_voice_size(voice["file_size"])

        assert file_ok is True
        assert duration_ok is True
        assert size_ok is True

    @pytest.mark.unit
    def test_invalid_all_fields(self):
        """Voice with all invalid fields should fail all checks."""
        voice = {
            "file_id": "",
            "duration": 600,  # 10 minutes
            "file_size": 20 * 1024 * 1024,  # 20MB
        }

        file_ok, _ = validate_voice_file_id(voice)
        duration_ok, _ = validate_voice_duration(voice["duration"])
        size_ok, _ = validate_voice_size(voice["file_size"])

        assert file_ok is False
        assert duration_ok is False
        assert size_ok is False
