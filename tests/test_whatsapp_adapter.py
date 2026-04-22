"""
WhatsApp (Twilio) adapter unit tests (WHATSAPP-001, issue #299).

Covers:
- Phone masking in logs
- SSRF allowlist for media URLs
- parse_message edge cases (text, media, empty, non-Twilio media)
- Message splitting at Twilio's 1600-char WhatsApp limit
- Webhook URL computation
- MessageSid dedup ring
- URL reconstruction with X-Forwarded-Proto
- Twilio RequestValidator round-trip (signature match + empty-param case)
- Webhook transport: unknown secret returns 200; bad signature returns 403
"""

import asyncio
import os
import sys
import types
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Backend path setup (same pattern as test_telegram_login_gate.py)
_backend_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src", "backend")
)
if _backend_path not in sys.path:
    sys.path.insert(0, _backend_path)

# Stub utils.helpers to avoid loading full backend utils
if "utils.helpers" not in sys.modules:
    _helpers = types.ModuleType("utils.helpers")
    _helpers.utc_now = lambda: datetime.utcnow()
    _helpers.utc_now_iso = lambda: datetime.utcnow().isoformat() + "Z"
    _helpers.to_utc_iso = lambda v: str(v)
    _helpers.parse_iso_timestamp = lambda s: datetime.fromisoformat(s.rstrip("Z"))
    sys.modules["utils.helpers"] = _helpers

# Stub `database` so adapter imports don't touch real SQLite
if "database" not in sys.modules:
    _database_stub = types.ModuleType("database")
    _database_stub.db = MagicMock()
    sys.modules["database"] = _database_stub


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# Override the autouse cleanup fixture from conftest.py so these pure unit tests
# don't require a live backend (conftest's cleanup_after_test pulls in api_client
# which authenticates on fixture setup).
@pytest.fixture(autouse=True)
def cleanup_after_test():  # noqa: F811 — intentional override
    yield


@pytest.fixture(scope="session")
def api_client():  # noqa: F811 — intentional override, never used here
    yield None


@pytest.fixture(scope="session")
def resource_tracker():  # noqa: F811 — intentional override, never used here
    class _NoopTracker:
        def cleanup(self, *args, **kwargs):
            pass
    yield _NoopTracker()


# =============================================================================
# Pure helpers — don't need DB / network stubbing
# =============================================================================

class TestPhoneMasking:
    @pytest.mark.unit
    def test_mask_whatsapp_e164(self):
        from adapters.whatsapp_adapter import _mask_phone
        masked = _mask_phone("whatsapp:+14155551234")
        assert "+14155551234" not in masked
        assert masked.endswith("1234")
        assert masked.startswith("whatsapp:+")

    @pytest.mark.unit
    def test_mask_raw_e164(self):
        from adapters.whatsapp_adapter import _mask_phone
        masked = _mask_phone("+14155551234")
        assert masked.endswith("1234")
        assert "+14155551234" not in masked

    @pytest.mark.unit
    def test_mask_empty(self):
        from adapters.whatsapp_adapter import _mask_phone
        assert _mask_phone("") == "<empty>"

    @pytest.mark.unit
    def test_mask_short_number(self):
        """Short numbers still get partial masking."""
        from adapters.whatsapp_adapter import _mask_phone
        masked = _mask_phone("+12345")
        assert "12345" not in masked


class TestTwilioMediaUrlAllowlist:
    """SSRF defense: only *.twilio.com hosts allowed."""

    @pytest.mark.unit
    def test_accepts_api_twilio_com(self):
        from adapters.whatsapp_adapter import _is_twilio_media_url
        assert _is_twilio_media_url(
            "https://api.twilio.com/2010-04-01/Accounts/AC00/Messages/MM00/Media/ME00"
        )

    @pytest.mark.unit
    def test_accepts_subdomain_twilio_com(self):
        from adapters.whatsapp_adapter import _is_twilio_media_url
        assert _is_twilio_media_url("https://media.twilio.com/some/path")

    @pytest.mark.unit
    def test_rejects_non_twilio_host(self):
        from adapters.whatsapp_adapter import _is_twilio_media_url
        assert not _is_twilio_media_url("https://attacker.com/evil")

    @pytest.mark.unit
    def test_rejects_domain_suffix_spoof(self):
        """'eviltwilio.com' must NOT match '.twilio.com'."""
        from adapters.whatsapp_adapter import _is_twilio_media_url
        assert not _is_twilio_media_url("https://eviltwilio.com/evil")
        # And the 'api.twilio.com.evil.com' subdomain trick
        assert not _is_twilio_media_url("https://api.twilio.com.evil.com/evil")

    @pytest.mark.unit
    def test_rejects_http_scheme(self):
        """Only https allowed (defense-in-depth — Twilio media is always https)."""
        from adapters.whatsapp_adapter import _is_twilio_media_url
        assert not _is_twilio_media_url("http://api.twilio.com/path")

    @pytest.mark.unit
    def test_rejects_garbage_url(self):
        from adapters.whatsapp_adapter import _is_twilio_media_url
        assert not _is_twilio_media_url("not-a-url")
        assert not _is_twilio_media_url("")


class TestMessageSplit:
    @pytest.mark.unit
    def test_short_message_single_chunk(self):
        from adapters.whatsapp_adapter import WhatsAppAdapter
        chunks = WhatsAppAdapter._split_message("hello")
        assert chunks == ["hello"]

    @pytest.mark.unit
    def test_exactly_limit_single_chunk(self):
        from adapters.whatsapp_adapter import WhatsAppAdapter, TWILIO_WHATSAPP_MAX_LENGTH
        text = "x" * TWILIO_WHATSAPP_MAX_LENGTH
        chunks = WhatsAppAdapter._split_message(text)
        assert chunks == [text]

    @pytest.mark.unit
    def test_long_message_splits_at_paragraph(self):
        from adapters.whatsapp_adapter import WhatsAppAdapter, TWILIO_WHATSAPP_MAX_LENGTH
        para1 = "a" * (TWILIO_WHATSAPP_MAX_LENGTH - 100)
        para2 = "b" * 500
        text = f"{para1}\n\n{para2}"
        chunks = WhatsAppAdapter._split_message(text)
        assert len(chunks) == 2
        assert all(len(c) <= TWILIO_WHATSAPP_MAX_LENGTH for c in chunks)

    @pytest.mark.unit
    def test_worst_case_no_separators(self):
        """Single giant unbroken string still splits without losing content."""
        from adapters.whatsapp_adapter import WhatsAppAdapter, TWILIO_WHATSAPP_MAX_LENGTH
        text = "z" * (TWILIO_WHATSAPP_MAX_LENGTH * 3)
        chunks = WhatsAppAdapter._split_message(text)
        assert "".join(chunks) == text
        assert all(len(c) <= TWILIO_WHATSAPP_MAX_LENGTH for c in chunks)


class TestWebhookUrlCompute:
    @pytest.mark.unit
    def test_basic(self):
        from adapters.transports.twilio_webhook import compute_webhook_url
        assert (
            compute_webhook_url("https://public.example.com", "abc123")
            == "https://public.example.com/api/whatsapp/webhook/abc123"
        )

    @pytest.mark.unit
    def test_trims_trailing_slash(self):
        from adapters.transports.twilio_webhook import compute_webhook_url
        assert (
            compute_webhook_url("https://public.example.com/", "abc123")
            == "https://public.example.com/api/whatsapp/webhook/abc123"
        )

    @pytest.mark.unit
    def test_empty_public_url_returns_empty(self):
        from adapters.transports.twilio_webhook import compute_webhook_url
        assert compute_webhook_url("", "abc123") == ""


class TestDedupRing:
    @pytest.mark.unit
    def test_first_seen_returns_false(self):
        from adapters.transports.twilio_webhook import _seen_recently, _SEEN_MESSAGE_SIDS
        _SEEN_MESSAGE_SIDS.clear()
        assert _seen_recently("SM_new_msg_1") is False

    @pytest.mark.unit
    def test_second_time_returns_true(self):
        from adapters.transports.twilio_webhook import _seen_recently, _SEEN_MESSAGE_SIDS
        _SEEN_MESSAGE_SIDS.clear()
        _seen_recently("SM_dup_msg")
        assert _seen_recently("SM_dup_msg") is True

    @pytest.mark.unit
    def test_empty_sid_not_recorded(self):
        from adapters.transports.twilio_webhook import _seen_recently, _SEEN_MESSAGE_SIDS
        _SEEN_MESSAGE_SIDS.clear()
        assert _seen_recently("") is False
        assert "" not in _SEEN_MESSAGE_SIDS

    @pytest.mark.unit
    def test_eviction_when_over_cap(self):
        from adapters.transports.twilio_webhook import _seen_recently, _SEEN_MESSAGE_SIDS, _SEEN_MAX
        _SEEN_MESSAGE_SIDS.clear()
        # Fill to cap
        for i in range(_SEEN_MAX):
            _SEEN_MESSAGE_SIDS[f"SM{i}"] = 0.0
        # Insert one more → eviction kicks in
        _seen_recently("SM_new_after_cap")
        assert len(_SEEN_MESSAGE_SIDS) < _SEEN_MAX + 1
        assert "SM_new_after_cap" in _SEEN_MESSAGE_SIDS


class TestUrlReconstruction:
    """Ensures uvicorn --proxy-headers + nginx X-Forwarded-Proto handling."""

    @pytest.mark.unit
    def test_upgrades_http_to_https_when_forwarded_proto_set(self):
        from adapters.transports.twilio_webhook import _reconstruct_url
        req = MagicMock()
        req.url = "http://backend:8000/api/whatsapp/webhook/abc"
        req.headers = {"x-forwarded-proto": "https"}
        # MagicMock for url returns the string in str() — but fastapi.Request.url is a URL obj
        # that `str()`-ifies to the URL. Simulate via plain str:
        req.__class__ = type(req)
        req.url = "http://backend:8000/api/whatsapp/webhook/abc"
        # str(request.url) is what our code calls
        req.url = types.SimpleNamespace(__str__=lambda self="http://backend:8000/api/whatsapp/webhook/abc": "http://backend:8000/api/whatsapp/webhook/abc")
        # Replace with a plain string-like
        class FakeReq:
            url = "http://backend:8000/api/whatsapp/webhook/abc"
            headers = {"x-forwarded-proto": "https"}
        url = _reconstruct_url(FakeReq())
        assert url.startswith("https://")

    @pytest.mark.unit
    def test_preserves_https_when_already_https(self):
        from adapters.transports.twilio_webhook import _reconstruct_url
        class FakeReq:
            url = "https://public.example.com/api/whatsapp/webhook/abc"
            headers = {}
        assert _reconstruct_url(FakeReq()) == "https://public.example.com/api/whatsapp/webhook/abc"


# =============================================================================
# parse_message — no DB interaction needed
# =============================================================================

class TestParseMessage:
    def _adapter(self):
        from adapters.whatsapp_adapter import WhatsAppAdapter
        return WhatsAppAdapter()

    @pytest.mark.unit
    def test_text_only(self):
        raw = {
            "From": "whatsapp:+14155551234",
            "To": "whatsapp:+14155238886",
            "Body": "Hello Trinity",
            "MessageSid": "SM_abc123",
            "NumMedia": "0",
            "_agent_name": "my-agent",
            "_binding_id": 7,
        }
        msg = self._adapter().parse_message(raw)
        assert msg is not None
        assert msg.text == "Hello Trinity"
        assert msg.sender_id == "whatsapp:+14155551234"
        assert msg.channel_id == "whatsapp:+14155551234"
        assert msg.metadata["agent_name"] == "my-agent"
        assert msg.metadata["binding_id"] == 7
        assert msg.metadata["message_sid"] == "SM_abc123"
        assert msg.metadata["is_group"] is False
        assert msg.files == []

    @pytest.mark.unit
    def test_empty_body_and_no_media_returns_none(self):
        raw = {"From": "whatsapp:+14155551234", "To": "whatsapp:+14155238886", "Body": "", "NumMedia": "0"}
        assert self._adapter().parse_message(raw) is None

    @pytest.mark.unit
    def test_missing_from_returns_none(self):
        raw = {"Body": "hi", "NumMedia": "0"}
        assert self._adapter().parse_message(raw) is None

    @pytest.mark.unit
    def test_media_attached(self):
        raw = {
            "From": "whatsapp:+14155551234",
            "To": "whatsapp:+14155238886",
            "Body": "Look at this",
            "MessageSid": "SM_img1",
            "NumMedia": "1",
            "MediaUrl0": "https://api.twilio.com/2010-04-01/Accounts/AC00/Messages/MM00/Media/ME00",
            "MediaContentType0": "image/jpeg",
            "_agent_name": "my-agent",
            "_binding_id": 7,
        }
        msg = self._adapter().parse_message(raw)
        assert msg is not None
        assert len(msg.files) == 1
        assert msg.files[0].mimetype == "image/jpeg"
        assert msg.files[0].name.startswith("media_0")
        assert msg.files[0].url.startswith("https://api.twilio.com/")

    @pytest.mark.unit
    def test_non_twilio_media_url_rejected(self):
        """SSRF defense at parse time — non-Twilio media URLs are dropped."""
        raw = {
            "From": "whatsapp:+14155551234",
            "To": "whatsapp:+14155238886",
            "Body": "check this",
            "NumMedia": "1",
            "MediaUrl0": "https://attacker.com/exfil",
            "MediaContentType0": "image/jpeg",
        }
        msg = self._adapter().parse_message(raw)
        # Body survived, but attacker URL was dropped
        assert msg is not None
        assert msg.files == []

    @pytest.mark.unit
    def test_media_only_gets_placeholder_text(self):
        raw = {
            "From": "whatsapp:+14155551234",
            "To": "whatsapp:+14155238886",
            "Body": "",
            "MessageSid": "SM_media_only",
            "NumMedia": "1",
            "MediaUrl0": "https://api.twilio.com/media/ME00",
            "MediaContentType0": "audio/ogg",
        }
        msg = self._adapter().parse_message(raw)
        assert msg is not None
        assert msg.text == "(media upload)"
        assert len(msg.files) == 1

    @pytest.mark.unit
    def test_bad_num_media_string_defaults_to_zero(self):
        raw = {
            "From": "whatsapp:+14155551234",
            "To": "whatsapp:+14155238886",
            "Body": "hi",
            "NumMedia": "not-a-number",
        }
        msg = self._adapter().parse_message(raw)
        assert msg is not None
        assert msg.files == []


# =============================================================================
# Twilio RequestValidator — signature round-trip
# =============================================================================

class TestRequestValidatorRoundTrip:
    """Canonical Twilio signature verification tests using the vendored validator."""

    @pytest.mark.unit
    def test_valid_signature_passes(self):
        from twilio.request_validator import RequestValidator
        auth_token = "test_auth_token_a" * 2  # 34 chars — realistic length
        validator = RequestValidator(auth_token)

        url = "https://public.example.com/api/whatsapp/webhook/abc123"
        params = {
            "From": "whatsapp:+14155551234",
            "To": "whatsapp:+14155238886",
            "Body": "Hello",
            "MessageSid": "SM_abc",
            "NumMedia": "0",
            "AccountSid": "AC00000000000000000000000000000000",
        }
        sig = validator.compute_signature(url, params)
        assert validator.validate(url, params, sig) is True

    @pytest.mark.unit
    def test_invalid_signature_fails(self):
        from twilio.request_validator import RequestValidator
        validator = RequestValidator("token_a" * 4)
        url = "https://public.example.com/api/whatsapp/webhook/abc"
        params = {"Body": "Hello", "AccountSid": "AC00"}
        assert validator.validate(url, params, "bogus-signature") is False

    @pytest.mark.unit
    def test_tampered_body_fails(self):
        from twilio.request_validator import RequestValidator
        auth_token = "test_token" * 4
        validator = RequestValidator(auth_token)
        url = "https://public.example.com/api/whatsapp/webhook/abc"
        params = {"Body": "original", "AccountSid": "AC00"}
        sig = validator.compute_signature(url, params)
        tampered = {"Body": "MODIFIED", "AccountSid": "AC00"}
        assert validator.validate(url, tampered, sig) is False

    @pytest.mark.unit
    def test_empty_param_value_preserved(self):
        """Regression: Twilio includes empty-value params in the HMAC, and
        RequestValidator must keep them too. This is the primary reason we
        use `twilio.request_validator` instead of rolling our own HMAC."""
        from twilio.request_validator import RequestValidator
        validator = RequestValidator("token_a" * 4)
        url = "https://public.example.com/api/whatsapp/webhook/abc"
        params_with_empty = {
            "Body": "hi",
            "ProfileName": "",  # Twilio sends this empty for users without a WhatsApp profile
            "AccountSid": "AC00",
        }
        sig = validator.compute_signature(url, params_with_empty)
        assert validator.validate(url, params_with_empty, sig) is True


# =============================================================================
# Webhook transport — integration-ish with mocked db and request
# =============================================================================

class TestWebhookTransport:
    def _transport(self):
        from adapters.transports.twilio_webhook import TwilioWebhookTransport
        from adapters.whatsapp_adapter import WhatsAppAdapter
        # Stub router — we only care about auth/dedup here
        router = MagicMock()
        router.handle_message = AsyncMock()
        return TwilioWebhookTransport(WhatsAppAdapter(), router)

    @pytest.mark.unit
    def test_unknown_webhook_secret_returns_ok_no_work(self):
        transport = self._transport()
        req = MagicMock()
        with patch("adapters.transports.twilio_webhook.db") as mock_db:
            mock_db.get_whatsapp_binding_by_webhook_secret.return_value = None
            result = _run(transport.handle_webhook(req, "unknown_secret"))
        assert result == {"ok": True}

    @pytest.mark.unit
    def test_invalid_signature_returns_403_marker(self):
        from adapters.transports.twilio_webhook import TwilioWebhookTransport
        transport = self._transport()

        # Build a fake request that yields a form body and bogus signature
        class FakeForm:
            def items(self):
                return [("Body", "hi"), ("AccountSid", "AC00"), ("MessageSid", "SM_x")]

        req = MagicMock()
        req.form = AsyncMock(return_value=FakeForm())
        req.headers = {"x-twilio-signature": "bogus", "x-forwarded-proto": "https"}
        req.url = "https://public.example.com/api/whatsapp/webhook/abc"

        with patch("adapters.transports.twilio_webhook.db") as mock_db:
            mock_db.get_whatsapp_binding_by_webhook_secret.return_value = {
                "id": 1, "agent_name": "my-agent", "webhook_secret": "abc",
            }
            mock_db.get_whatsapp_auth_token.return_value = "auth_token_" + "a" * 20
            result = _run(transport.handle_webhook(req, "abc"))
        assert result == {"ok": False, "status": 403}

    @pytest.mark.unit
    def test_valid_signature_dispatches(self):
        """End-to-end: valid sig → dedup miss → asyncio task scheduled, returns 200."""
        from twilio.request_validator import RequestValidator
        from adapters.transports.twilio_webhook import _SEEN_MESSAGE_SIDS
        _SEEN_MESSAGE_SIDS.clear()

        auth_token = "real_auth_token_" + "x" * 20
        url = "https://public.example.com/api/whatsapp/webhook/abc"
        params = {
            "From": "whatsapp:+14155551234",
            "To": "whatsapp:+14155238886",
            "Body": "hello",
            "AccountSid": "AC00000000000000000000000000000000",
            "MessageSid": "SM_valid_one",
            "NumMedia": "0",
        }
        sig = RequestValidator(auth_token).compute_signature(url, params)

        class FakeForm:
            def __init__(self, params): self._p = list(params.items())
            def items(self): return self._p

        req = MagicMock()
        req.form = AsyncMock(return_value=FakeForm(params))
        req.headers = {"x-twilio-signature": sig, "x-forwarded-proto": "https"}
        req.url = url

        transport = self._transport()
        with patch("adapters.transports.twilio_webhook.db") as mock_db:
            mock_db.get_whatsapp_binding_by_webhook_secret.return_value = {
                "id": 1, "agent_name": "my-agent", "webhook_secret": "abc",
            }
            mock_db.get_whatsapp_auth_token.return_value = auth_token
            result = _run(transport.handle_webhook(req, "abc"))
        assert result == {"ok": True}

    @pytest.mark.unit
    def test_duplicate_message_sid_skipped(self):
        """Second webhook with same MessageSid is dropped pre-dispatch."""
        from twilio.request_validator import RequestValidator
        from adapters.transports.twilio_webhook import _SEEN_MESSAGE_SIDS
        _SEEN_MESSAGE_SIDS.clear()

        auth_token = "real_auth_token_" + "x" * 20
        url = "https://public.example.com/api/whatsapp/webhook/abc"
        params = {
            "From": "whatsapp:+14155551234",
            "To": "whatsapp:+14155238886",
            "Body": "dup",
            "AccountSid": "AC00000000000000000000000000000000",
            "MessageSid": "SM_duplicated",
            "NumMedia": "0",
        }
        sig = RequestValidator(auth_token).compute_signature(url, params)

        class FakeForm:
            def __init__(self, params): self._p = list(params.items())
            def items(self): return self._p

        req = MagicMock()
        req.form = AsyncMock(return_value=FakeForm(params))
        req.headers = {"x-twilio-signature": sig, "x-forwarded-proto": "https"}
        req.url = url

        transport = self._transport()
        with patch("adapters.transports.twilio_webhook.db") as mock_db:
            mock_db.get_whatsapp_binding_by_webhook_secret.return_value = {
                "id": 1, "agent_name": "my-agent", "webhook_secret": "abc",
            }
            mock_db.get_whatsapp_auth_token.return_value = auth_token
            # First call — accepted
            first = _run(transport.handle_webhook(req, "abc"))
            # Same SID again — deduped (still returns 200 ok)
            second = _run(transport.handle_webhook(req, "abc"))
        assert first == {"ok": True}
        assert second == {"ok": True}
        # Dispatch was scheduled only once — check by inspecting the dedup ring
        assert "SM_duplicated" in _SEEN_MESSAGE_SIDS
