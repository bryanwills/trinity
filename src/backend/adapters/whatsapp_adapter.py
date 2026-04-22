"""
WhatsApp channel adapter via Twilio (WHATSAPP-001).

Twilio delivers inbound WhatsApp messages as form-encoded POSTs signed with
HMAC-SHA1. Outbound messages go via Twilio REST (`POST /Messages.json`) with
HTTP Basic auth (AccountSid:AuthToken).

Scope: Phase 1 MVP — direct messages only. Group chats are not supported by
Twilio's WhatsApp API (see issue #299). Access-control integration (#311) is
wired minimally via the `verified_email` columns; richer /login flows are
Phase 2.
"""

import base64
import logging
from typing import List, Optional
from urllib.parse import urlparse

import httpx

from database import db
from adapters.base import (
    ChannelAdapter,
    ChannelResponse,
    FileAttachment,
    NormalizedMessage,
)

logger = logging.getLogger(__name__)

# Twilio's message body limit for WhatsApp
TWILIO_WHATSAPP_MAX_LENGTH = 1600

# Twilio REST API base (outbound send)
TWILIO_API_BASE = "https://api.twilio.com"

# SSRF allowlist for media downloads — only Twilio-hosted URLs permitted.
_TWILIO_MEDIA_ALLOWED_HOST_SUFFIXES = (".twilio.com",)


def _mask_phone(phone: str) -> str:
    """Mask a phone number for safe logging: 'whatsapp:+14155551234' → 'whatsapp:+141***1234'."""
    if not phone:
        return "<empty>"
    # Keep first 4 digits after '+' and last 4
    # Works for 'whatsapp:+14155551234' and '+14155551234'
    if "+" not in phone:
        return f"{phone[:4]}***"
    prefix, _, rest = phone.partition("+")
    if len(rest) <= 8:
        return f"{prefix}+***{rest[-2:]}"
    return f"{prefix}+{rest[:3]}***{rest[-4:]}"


def _is_twilio_media_url(url: str) -> bool:
    """Check if a URL is hosted on a Twilio domain (SSRF defense)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        return any(host == s.lstrip(".") or host.endswith(s) for s in _TWILIO_MEDIA_ALLOWED_HOST_SUFFIXES)
    except Exception:
        return False


class WhatsAppAdapter(ChannelAdapter):
    """WhatsApp implementation of ChannelAdapter, backed by Twilio."""

    # =========================================================================
    # ChannelAdapter interface — identity & routing
    # =========================================================================

    @property
    def channel_type(self) -> str:
        return "whatsapp"

    def get_rate_key(self, message: NormalizedMessage) -> str:
        binding_id = message.metadata.get("binding_id", "unknown")
        return f"whatsapp:{binding_id}:{message.sender_id}"

    def get_session_identifier(self, message: NormalizedMessage) -> str:
        binding_id = message.metadata.get("binding_id", "unknown")
        # sender_id IS the WhatsApp phone (e.g. 'whatsapp:+14155551234')
        return f"{binding_id}:{message.sender_id}"

    def get_source_identifier(self, message: NormalizedMessage) -> str:
        return f"whatsapp:{message.sender_id}"

    def get_bot_token(self, message: NormalizedMessage) -> Optional[str]:
        """
        For WhatsApp, the "bot token" composite is AccountSid + AuthToken, used
        for HTTP Basic auth against Twilio. We return a 'sid:token' string here
        so `message_router` can pass it through response metadata unchanged —
        the actual auth header is constructed in `send_response`.
        """
        agent_name = message.metadata.get("agent_name")
        if not agent_name:
            return None
        binding = db.get_whatsapp_binding(agent_name)
        if not binding:
            return None
        auth_token = db.get_whatsapp_auth_token(agent_name)
        if not auth_token:
            return None
        return f"{binding['account_sid']}:{auth_token}"

    # =========================================================================
    # ChannelAdapter interface — message processing
    # =========================================================================

    def parse_message(self, raw_event: dict) -> Optional[NormalizedMessage]:
        """
        Parse a Twilio WhatsApp form-encoded payload into a NormalizedMessage.

        Twilio fields of interest:
        - From            — sender, 'whatsapp:+E164'
        - To              — our bound from_number
        - Body            — message text
        - MessageSid      — globally unique per delivery attempt (dedup key)
        - ProfileName     — sender display name (optional)
        - NumMedia        — count of attached media
        - MediaUrl{N}     — Twilio-hosted media URL (requires Basic auth)
        - MediaContentType{N}
        """
        sender = raw_event.get("From", "").strip()
        if not sender:
            return None

        body = (raw_event.get("Body") or "").strip()
        wa_user_name = raw_event.get("ProfileName") or None
        message_sid = raw_event.get("MessageSid", "")

        files: List[FileAttachment] = []
        try:
            num_media = int(raw_event.get("NumMedia", "0") or "0")
        except ValueError:
            num_media = 0

        for i in range(num_media):
            media_url = raw_event.get(f"MediaUrl{i}") or ""
            if not media_url:
                continue
            # Defense-in-depth: reject non-Twilio URLs at parse time.
            if not _is_twilio_media_url(media_url):
                logger.warning(
                    "[WHATSAPP] Rejecting non-Twilio media URL at parse time: %s",
                    urlparse(media_url).hostname,
                )
                continue
            mimetype = raw_event.get(f"MediaContentType{i}") or "application/octet-stream"
            # Twilio doesn't provide filenames; synthesize one from index/mimetype.
            ext = mimetype.split("/")[-1].split(";")[0] or "bin"
            files.append(FileAttachment(
                id=f"{message_sid}-{i}",
                name=f"media_{i}.{ext}",
                mimetype=mimetype,
                size=0,  # Twilio doesn't send size in webhook; trust the router's post-download size check
                url=media_url,
            ))

        # No text and no media → nothing to process
        if not body and not files:
            return None

        if not body and files:
            body = "(media upload)"

        agent_name = raw_event.get("_agent_name", "")
        binding_id = raw_event.get("_binding_id")

        return NormalizedMessage(
            sender_id=sender,
            text=body,
            channel_id=sender,  # DMs: the "channel" is the sender's phone
            thread_id=message_sid,
            timestamp="",  # Twilio webhook doesn't include message timestamp directly
            files=files,
            metadata={
                "agent_name": agent_name,
                "binding_id": binding_id,
                "message_sid": message_sid,
                "from_number": raw_event.get("To", ""),
                "wa_user_name": wa_user_name,
                "is_group": False,
                "raw_event": raw_event,
            },
        )

    async def send_response(
        self,
        channel_id: str,
        response: ChannelResponse,
        thread_id: Optional[str] = None,
    ) -> None:
        """Send a WhatsApp message via Twilio REST API."""
        composite = response.metadata.get("bot_token") or ""
        agent_name = response.metadata.get("agent_name", "")

        account_sid, _, auth_token = composite.partition(":")
        if not account_sid or not auth_token:
            logger.error(
                "[WHATSAPP] Missing Twilio credentials in response metadata for agent=%s",
                agent_name,
            )
            return

        binding = db.get_whatsapp_binding(agent_name) if agent_name else None
        if not binding:
            logger.error("[WHATSAPP] No binding for agent=%s when sending response", agent_name)
            return

        text = response.text or ""
        if not text.strip():
            return

        chunks = self._split_message(text)

        for chunk in chunks:
            await self._send_message(
                account_sid=account_sid,
                auth_token=auth_token,
                from_number=binding["from_number"],
                messaging_service_sid=binding.get("messaging_service_sid"),
                to_number=channel_id,
                body=chunk,
            )

    async def get_agent_name(self, message: NormalizedMessage) -> Optional[str]:
        return message.metadata.get("agent_name") or None

    # =========================================================================
    # Unified access control (Issue #311) — minimal Phase 1 plumbing
    # =========================================================================

    async def resolve_verified_email(
        self, message: NormalizedMessage
    ) -> Optional[str]:
        """Look up verified email for this WhatsApp user (filled by Phase 2)."""
        agent_name = message.metadata.get("agent_name")
        if not agent_name:
            return None
        binding = db.get_whatsapp_binding(agent_name)
        if not binding:
            return None
        return db.get_whatsapp_verified_email(binding["id"], message.sender_id)

    # =========================================================================
    # File download — Twilio-hosted media with HTTP Basic auth
    # =========================================================================

    async def download_file(
        self, file: FileAttachment, message: NormalizedMessage
    ) -> Optional[bytes]:
        """
        Fetch Twilio-hosted media. The URL requires Basic auth with
        AccountSid:AuthToken.
        """
        if not _is_twilio_media_url(file.url):
            logger.warning(
                "[WHATSAPP] Refusing to download non-Twilio media URL (host=%s)",
                urlparse(file.url).hostname,
            )
            return None

        agent_name = message.metadata.get("agent_name", "")
        binding = db.get_whatsapp_binding(agent_name) if agent_name else None
        auth_token = db.get_whatsapp_auth_token(agent_name) if agent_name else None
        if not binding or not auth_token:
            logger.error("[WHATSAPP] No credentials to download media for agent=%s", agent_name)
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                resp = await client.get(
                    file.url,
                    auth=(binding["account_sid"], auth_token),
                )
                # Twilio sometimes 302-redirects to a signed S3 URL for large media.
                # Follow only if the redirect target is still Twilio-hosted.
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location", "")
                    if not _is_twilio_media_url(location):
                        logger.warning(
                            "[WHATSAPP] Refusing off-domain media redirect to host=%s",
                            urlparse(location).hostname,
                        )
                        return None
                    # Follow once, without auth (signed URL carries its own creds)
                    resp = await client.get(location)

                if resp.status_code != 200:
                    logger.error(
                        "[WHATSAPP] Media download failed (status=%d, file=%s)",
                        resp.status_code, file.name,
                    )
                    return None
                return resp.content
        except httpx.TimeoutException:
            logger.error("[WHATSAPP] Timeout downloading media %s", file.name)
            return None
        except Exception as e:
            logger.error("[WHATSAPP] Error downloading media %s: %s", file.name, e)
            return None

    # =========================================================================
    # Twilio REST helpers
    # =========================================================================

    @staticmethod
    async def _send_message(
        account_sid: str,
        auth_token: str,
        from_number: str,
        messaging_service_sid: Optional[str],
        to_number: str,
        body: str,
    ) -> Optional[dict]:
        """POST a single message to Twilio's Messages endpoint.

        Prefers MessagingServiceSid if configured (handles sender selection
        server-side); falls back to explicit From.
        """
        url = f"{TWILIO_API_BASE}/2010-04-01/Accounts/{account_sid}/Messages.json"
        data = {
            "To": to_number,
            "Body": body,
        }
        if messaging_service_sid:
            data["MessagingServiceSid"] = messaging_service_sid
        else:
            data["From"] = from_number

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, data=data, auth=(account_sid, auth_token))
                if resp.status_code >= 400:
                    # Twilio error codes we care about:
                    #   63016 — message outside the 24-hour window (need template)
                    #   21408 — permission to send to this region not enabled
                    #   21211 — 'To' number is not a valid WhatsApp number
                    body_masked = resp.text[:500] if resp.text else ""
                    logger.error(
                        "[WHATSAPP] Twilio send failed (status=%d, to=%s): %s",
                        resp.status_code, _mask_phone(to_number), body_masked,
                    )
                    return None
                return resp.json()
        except Exception as e:
            logger.error("[WHATSAPP] Twilio send error (to=%s): %s", _mask_phone(to_number), e)
            return None

    @staticmethod
    def _split_message(text: str) -> List[str]:
        """Split text into chunks respecting Twilio's 1600-char WhatsApp limit."""
        if len(text) <= TWILIO_WHATSAPP_MAX_LENGTH:
            return [text]
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= TWILIO_WHATSAPP_MAX_LENGTH:
                chunks.append(remaining)
                break
            split_at = TWILIO_WHATSAPP_MAX_LENGTH
            for sep in ("\n\n", "\n", ". ", " "):
                idx = remaining.rfind(sep, 0, TWILIO_WHATSAPP_MAX_LENGTH)
                if idx > TWILIO_WHATSAPP_MAX_LENGTH // 2:
                    split_at = idx + len(sep)
                    break
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]
        return chunks
