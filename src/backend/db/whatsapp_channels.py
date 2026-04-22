"""
Database operations for WhatsApp (Twilio) bindings and chat tracking (WHATSAPP-001).

Handles:
- Bot bindings (one Twilio sender per agent; AuthToken encrypted at rest)
- Chat link tracking (WhatsApp phone → session mapping)
- Verified-email storage for unified access control (#311 — Phase 2 uses these columns)
"""

import logging
import secrets
from typing import List, Optional

from db.connection import get_db_connection
from utils.helpers import utc_now_iso

logger = logging.getLogger(__name__)

# Twilio Sandbox shared sender number — auto-detect sandbox mode.
_TWILIO_SANDBOX_FROM_NUMBER = "whatsapp:+14155238886"


def _is_sandbox_number(from_number: str) -> bool:
    """True if the from_number is the shared Twilio WhatsApp Sandbox sender."""
    return from_number.strip() == _TWILIO_SANDBOX_FROM_NUMBER


class WhatsAppChannelOperations:
    """Operations for WhatsApp/Twilio bindings and chat links."""

    # =========================================================================
    # Encryption helpers (same pattern as Slack/Telegram)
    # =========================================================================

    def _get_encryption_service(self):
        from services.credential_encryption import CredentialEncryptionService
        return CredentialEncryptionService()

    def _encrypt_auth_token(self, auth_token: str) -> str:
        svc = self._get_encryption_service()
        return svc.encrypt({"auth_token": auth_token})

    def _decrypt_auth_token(self, encrypted: str) -> Optional[str]:
        try:
            svc = self._get_encryption_service()
            decrypted = svc.decrypt(encrypted)
            return decrypted.get("auth_token")
        except Exception as e:
            logger.error(f"Failed to decrypt Twilio AuthToken: {e}")
            return None

    # =========================================================================
    # Binding Operations
    # =========================================================================

    def create_binding(
        self,
        agent_name: str,
        account_sid: str,
        auth_token: str,
        from_number: str,
        messaging_service_sid: Optional[str] = None,
        display_name: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        """Create or replace a WhatsApp (Twilio) binding for an agent."""
        webhook_secret = secrets.token_urlsafe(32)
        now = utc_now_iso()
        encrypted_token = self._encrypt_auth_token(auth_token)
        is_sandbox = 1 if _is_sandbox_number(from_number) else 0

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO whatsapp_bindings
                (agent_name, account_sid, auth_token_encrypted, from_number,
                 messaging_service_sid, display_name, is_sandbox, webhook_secret,
                 enabled, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(agent_name) DO UPDATE SET
                    account_sid = excluded.account_sid,
                    auth_token_encrypted = excluded.auth_token_encrypted,
                    from_number = excluded.from_number,
                    messaging_service_sid = excluded.messaging_service_sid,
                    display_name = excluded.display_name,
                    is_sandbox = excluded.is_sandbox,
                    webhook_secret = excluded.webhook_secret,
                    enabled = 1,
                    updated_at = excluded.updated_at
            """, (agent_name, account_sid, encrypted_token, from_number,
                  messaging_service_sid, display_name, is_sandbox,
                  webhook_secret, created_by, now, now))
            conn.commit()

        return self.get_binding_by_agent(agent_name)

    def get_binding_by_agent(self, agent_name: str) -> Optional[dict]:
        """Fetch binding by agent name. AuthToken stays encrypted."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, agent_name, account_sid, auth_token_encrypted,
                       from_number, messaging_service_sid, display_name,
                       is_sandbox, webhook_secret, webhook_url, enabled,
                       created_by, created_at, updated_at
                FROM whatsapp_bindings WHERE agent_name = ?
            """, (agent_name,))
            row = cursor.fetchone()
        return self._row_to_binding(row) if row else None

    def get_binding_by_webhook_secret(self, webhook_secret: str) -> Optional[dict]:
        """Resolve webhook_secret → binding for incoming webhook routing."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, agent_name, account_sid, auth_token_encrypted,
                       from_number, messaging_service_sid, display_name,
                       is_sandbox, webhook_secret, webhook_url, enabled,
                       created_by, created_at, updated_at
                FROM whatsapp_bindings WHERE webhook_secret = ?
            """, (webhook_secret,))
            row = cursor.fetchone()
        return self._row_to_binding(row) if row else None

    def get_decrypted_auth_token(self, agent_name: str) -> Optional[str]:
        binding = self.get_binding_by_agent(agent_name)
        if not binding:
            return None
        return self._decrypt_auth_token(binding["auth_token_encrypted"])

    def get_all_bindings(self) -> List[dict]:
        """All bindings (for startup reconciliation + webhook URL backfill)."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, agent_name, account_sid, auth_token_encrypted,
                       from_number, messaging_service_sid, display_name,
                       is_sandbox, webhook_secret, webhook_url, enabled,
                       created_by, created_at, updated_at
                FROM whatsapp_bindings
            """)
            rows = cursor.fetchall()
        return [self._row_to_binding(row) for row in rows]

    def update_webhook_url(self, agent_name: str, webhook_url: str) -> None:
        """Persist the canonical webhook URL for this binding (UI display)."""
        now = utc_now_iso()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE whatsapp_bindings
                SET webhook_url = ?, updated_at = ?
                WHERE agent_name = ?
            """, (webhook_url, now, agent_name))
            conn.commit()

    def delete_binding(self, agent_name: str) -> bool:
        """Delete a binding and cascade to chat_links."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM whatsapp_chat_links
                WHERE binding_id IN (
                    SELECT id FROM whatsapp_bindings WHERE agent_name = ?
                )
            """, (agent_name,))
            cursor.execute(
                "DELETE FROM whatsapp_bindings WHERE agent_name = ?",
                (agent_name,)
            )
            deleted = cursor.rowcount > 0
            conn.commit()
        return deleted

    # =========================================================================
    # Chat Link Operations
    # =========================================================================

    def get_or_create_chat_link(
        self,
        binding_id: int,
        wa_user_phone: str,
        wa_user_name: Optional[str] = None,
    ) -> dict:
        """Get or create a chat link for a WhatsApp user (by phone number)."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, binding_id, wa_user_phone, wa_user_name,
                       session_id, verified_email, verified_at,
                       message_count, last_active, created_at
                FROM whatsapp_chat_links
                WHERE binding_id = ? AND wa_user_phone = ?
            """, (binding_id, wa_user_phone))
            row = cursor.fetchone()

            if row:
                return self._row_to_chat_link(row)

            now = utc_now_iso()
            cursor.execute("""
                INSERT INTO whatsapp_chat_links
                (binding_id, wa_user_phone, wa_user_name, message_count,
                 created_at, last_active)
                VALUES (?, ?, ?, 0, ?, ?)
            """, (binding_id, wa_user_phone, wa_user_name, now, now))
            conn.commit()

            cursor.execute("""
                SELECT id, binding_id, wa_user_phone, wa_user_name,
                       session_id, verified_email, verified_at,
                       message_count, last_active, created_at
                FROM whatsapp_chat_links
                WHERE binding_id = ? AND wa_user_phone = ?
            """, (binding_id, wa_user_phone))
            return self._row_to_chat_link(cursor.fetchone())

    def get_verified_email(self, binding_id: int, wa_user_phone: str) -> Optional[str]:
        """Return verified email for this phone, or None (#311 Phase 2)."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT verified_email
                FROM whatsapp_chat_links
                WHERE binding_id = ? AND wa_user_phone = ?
            """, (binding_id, wa_user_phone))
            row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def increment_message_count(self, chat_link_id: int) -> None:
        now = utc_now_iso()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE whatsapp_chat_links
                SET message_count = message_count + 1, last_active = ?
                WHERE id = ?
            """, (now, chat_link_id))
            conn.commit()

    # =========================================================================
    # Row converters
    # =========================================================================

    @staticmethod
    def _row_to_binding(row) -> dict:
        return {
            "id": row[0],
            "agent_name": row[1],
            "account_sid": row[2],
            "auth_token_encrypted": row[3],
            "from_number": row[4],
            "messaging_service_sid": row[5],
            "display_name": row[6],
            "is_sandbox": bool(row[7]),
            "webhook_secret": row[8],
            "webhook_url": row[9],
            "enabled": bool(row[10]),
            "created_by": row[11],
            "created_at": row[12],
            "updated_at": row[13],
        }

    @staticmethod
    def _row_to_chat_link(row) -> dict:
        return {
            "id": row[0],
            "binding_id": row[1],
            "wa_user_phone": row[2],
            "wa_user_name": row[3],
            "session_id": row[4],
            "verified_email": row[5],
            "verified_at": row[6],
            "message_count": row[7],
            "last_active": row[8],
            "created_at": row[9],
        }
