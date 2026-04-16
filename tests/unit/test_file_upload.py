"""
Tests for file upload support (Issue #354).

Tests Telegram adapter file parsing, download implementation,
and message router security validations.

Related flow: docs/memory/feature-flows/channel-adapters.md
"""

import os
import sys
import tempfile

# Set up temp database BEFORE any imports that trigger database initialization
_temp_dir = tempfile.mkdtemp()
os.environ.setdefault("TRINITY_DB_PATH", os.path.join(_temp_dir, "test.db"))

# Uses unit/conftest.py which sets up sys.path and handles utils shadowing

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Mark all tests as unit tests (no backend required)
pytestmark = pytest.mark.unit


class TestTelegramFileExtraction:
    """Test Telegram adapter file extraction from messages."""

    def test_extract_photo_largest_size(self):
        """Telegram photos: should extract largest size (last in array)."""
        from adapters.telegram_adapter import TelegramAdapter

        adapter = TelegramAdapter()

        # Telegram sends array of photo sizes, smallest to largest
        message = {
            "photo": [
                {"file_id": "small_id", "file_size": 1000, "width": 100, "height": 100},
                {"file_id": "medium_id", "file_size": 5000, "width": 500, "height": 500},
                {"file_id": "large_id", "file_size": 50000, "width": 1000, "height": 1000},
            ],
            "caption": "Test photo",
        }

        files = adapter._extract_files(message)

        assert len(files) == 1
        assert files[0].id == "large_id"
        assert files[0].size == 50000
        assert files[0].mimetype == "image/jpeg"
        assert files[0].name == "photo.jpg"

    def test_extract_document(self):
        """Telegram documents: should extract file metadata."""
        from adapters.telegram_adapter import TelegramAdapter

        adapter = TelegramAdapter()

        message = {
            "document": {
                "file_id": "doc_123",
                "file_name": "report.pdf",
                "mime_type": "application/pdf",
                "file_size": 102400,
            },
        }

        files = adapter._extract_files(message)

        assert len(files) == 1
        assert files[0].id == "doc_123"
        assert files[0].name == "report.pdf"
        assert files[0].mimetype == "application/pdf"
        assert files[0].size == 102400

    def test_extract_no_media(self):
        """Text-only message: should return empty list."""
        from adapters.telegram_adapter import TelegramAdapter

        adapter = TelegramAdapter()
        message = {"text": "Hello world"}

        files = adapter._extract_files(message)

        assert files == []

    def test_extract_photo_and_document(self):
        """Message with both photo and document: should extract both."""
        from adapters.telegram_adapter import TelegramAdapter

        adapter = TelegramAdapter()

        message = {
            "photo": [{"file_id": "photo_id", "file_size": 1000}],
            "document": {
                "file_id": "doc_id",
                "file_name": "data.csv",
                "mime_type": "text/csv",
                "file_size": 500,
            },
        }

        files = adapter._extract_files(message)

        assert len(files) == 2
        # Photo first (order from _extract_files)
        assert files[0].id == "photo_id"
        assert files[1].id == "doc_id"


class TestTelegramFileDownload:
    """Test Telegram adapter download_file implementation."""

    @pytest.mark.asyncio
    async def test_download_file_success(self):
        """Successful two-step download via Bot API."""
        from adapters.telegram_adapter import TelegramAdapter
        from adapters.base import FileAttachment, NormalizedMessage

        adapter = TelegramAdapter()

        file = FileAttachment(
            id="AgACAgIAAxk",
            name="photo.jpg",
            mimetype="image/jpeg",
            size=5000,
            url="AgACAgIAAxk",
        )

        message = NormalizedMessage(
            sender_id="123",
            text="test",
            channel_id="456",
            timestamp="1234567890",
            metadata={"agent_name": "test-agent"},
        )

        mock_file_content = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # JPEG header

        with patch("adapters.telegram_adapter.db") as mock_db, \
             patch("adapters.telegram_adapter.httpx.AsyncClient") as MockClient:

            mock_db.get_telegram_bot_token.return_value = "bot123:token"

            mock_client = AsyncMock()
            MockClient.return_value.__aenter__.return_value = mock_client

            # First call: getFile returns file_path
            mock_client.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"ok": True, "result": {"file_path": "photos/file_0.jpg"}},
            )

            # Second call: download returns file content
            mock_client.get.return_value = MagicMock(
                status_code=200,
                content=mock_file_content,
            )

            result = await adapter.download_file(file, message)

            assert result == mock_file_content
            mock_db.get_telegram_bot_token.assert_called_once_with("test-agent")

    @pytest.mark.asyncio
    async def test_download_file_no_agent_name(self):
        """Should return None if no agent_name in metadata."""
        from adapters.telegram_adapter import TelegramAdapter
        from adapters.base import FileAttachment, NormalizedMessage

        adapter = TelegramAdapter()

        file = FileAttachment(
            id="file_id",
            name="test.txt",
            mimetype="text/plain",
            size=100,
            url="file_id",
        )

        message = NormalizedMessage(
            sender_id="123",
            text="test",
            channel_id="456",
            timestamp="1234567890",
            metadata={},  # No agent_name
        )

        result = await adapter.download_file(file, message)

        assert result is None

    @pytest.mark.asyncio
    async def test_download_file_getfile_fails(self):
        """Should return None if getFile API fails."""
        from adapters.telegram_adapter import TelegramAdapter
        from adapters.base import FileAttachment, NormalizedMessage

        adapter = TelegramAdapter()

        file = FileAttachment(
            id="file_id",
            name="test.txt",
            mimetype="text/plain",
            size=100,
            url="file_id",
        )

        message = NormalizedMessage(
            sender_id="123",
            text="test",
            channel_id="456",
            timestamp="1234567890",
            metadata={"agent_name": "test-agent"},
        )

        with patch("adapters.telegram_adapter.db") as mock_db, \
             patch("adapters.telegram_adapter.httpx.AsyncClient") as MockClient:

            mock_db.get_telegram_bot_token.return_value = "bot123:token"

            mock_client = AsyncMock()
            MockClient.return_value.__aenter__.return_value = mock_client

            # getFile returns error
            mock_client.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"ok": False, "description": "File not found"},
            )

            result = await adapter.download_file(file, message)

            assert result is None


class TestMessageRouterFileValidation:
    """Test message router file validation (size, MIME)."""

    def test_format_file_size(self):
        """Test human-readable file size formatting."""
        from adapters.message_router import _format_file_size

        assert _format_file_size(500) == "500 B"
        assert _format_file_size(1024) == "1 KB"
        assert _format_file_size(1536) == "2 KB"  # Rounded
        assert _format_file_size(1048576) == "1.0 MB"
        assert _format_file_size(5242880) == "5.0 MB"

    def test_magic_available_flag(self):
        """Check that magic library availability is tracked."""
        from adapters import message_router

        # _MAGIC_AVAILABLE should be defined (True if python-magic installed)
        assert hasattr(message_router, "_MAGIC_AVAILABLE")
        assert isinstance(message_router._MAGIC_AVAILABLE, bool)


class TestParseMessageWithFiles:
    """Test parse_message populates files field correctly."""

    def test_parse_message_with_photo(self):
        """parse_message should populate files for photos."""
        from adapters.telegram_adapter import TelegramAdapter

        adapter = TelegramAdapter()

        raw_event = {
            "_bot_id": "bot123",
            "_bot_username": "testbot",
            "_agent_name": "test-agent",
            "message": {
                "message_id": 1,
                "from": {"id": 123, "is_bot": False, "username": "testuser"},
                "chat": {"id": 456, "type": "private"},
                "date": 1234567890,
                "photo": [
                    {"file_id": "photo_id", "file_size": 5000, "width": 800, "height": 600}
                ],
                "caption": "Check this out",
            },
        }

        result = adapter.parse_message(raw_event)

        assert result is not None
        assert len(result.files) == 1
        assert result.files[0].id == "photo_id"
        assert "photo" in result.text.lower() or "check this out" in result.text.lower()

    def test_parse_message_file_only_no_text(self):
        """parse_message should work with file-only messages (no caption)."""
        from adapters.telegram_adapter import TelegramAdapter

        adapter = TelegramAdapter()

        raw_event = {
            "_bot_id": "bot123",
            "_bot_username": "testbot",
            "_agent_name": "test-agent",
            "message": {
                "message_id": 1,
                "from": {"id": 123, "is_bot": False},
                "chat": {"id": 456, "type": "private"},
                "date": 1234567890,
                "document": {
                    "file_id": "doc_id",
                    "file_name": "data.json",
                    "mime_type": "application/json",
                    "file_size": 1000,
                },
                # No caption, no text
            },
        }

        result = adapter.parse_message(raw_event)

        assert result is not None
        assert len(result.files) == 1
        # Should have placeholder text for file-only messages
        assert result.text  # Not empty
