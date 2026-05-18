"""Guerrilla Mail API wrapper for disposable email generation."""

import logging
import re
import time
from typing import Optional

import requests

from freebie_hunter.config import GUERRILLA_API, USER_AGENT

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}


class GuerrillaMailError(Exception):
    """Raised when the Guerrilla Mail API returns an error."""
    pass


class GuerrillaMail:
    """Wrapper around the Guerrilla Mail REST API.

    Usage:
        gm = GuerrillaMail()
        email = gm.get_email_address()
        messages = gm.fetch_email()
        gm.set_email_user("myname")
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._sid_token: Optional[str] = None
        self._email_addr: Optional[str] = None

    def _request(self, params: dict, allow_empty: bool = False) -> dict:
        """Make an API request with error handling.

        Args:
            params: API parameters
            allow_empty: If True, return empty dict on non-JSON instead of raising.
        """
        params["agent"] = "freebie_hunter_1.0"
        if self._sid_token:
            params["sid_token"] = self._sid_token

        try:
            resp = self.session.get(GUERRILLA_API, params=params, timeout=15)
            resp.raise_for_status()

            text = resp.text.strip()
            if not text:
                if allow_empty:
                    logger.debug("Guerrilla Mail returned empty response")
                    return {}
                raise GuerrillaMailError("Empty response from Guerrilla Mail API")

            try:
                data = resp.json()
            except ValueError:
                if allow_empty:
                    logger.debug(f"Guerrilla Mail returned non-JSON: {text[:100]}")
                    return {}
                raise GuerrillaMailError(f"Invalid JSON response: {text[:200]}")

            return data
        except requests.RequestException as e:
            logger.error(f"Guerrilla Mail API request failed: {e}")
            raise GuerrillaMailError(f"API request failed: {e}")

    def _update_state(self, data: dict) -> None:
        """Update internal state from API response."""
        if "sid_token" in data:
            self._sid_token = data["sid_token"]
        if "email_addr" in data:
            self._email_addr = data["email_addr"]

    def get_email_address(self) -> str:
        """Get a new temporary email address.

        Returns:
            The generated email address string.
        """
        logger.info("Getting new Guerrilla Mail address...")
        try:
            data = self._request({"f": "get_email_address"})
            self._update_state(data)
            logger.info(f"Got email: {self._email_addr}")
            return self._email_addr
        except Exception:
            # Try with a specific user as fallback
            import random
            import string
            random_user = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
            logger.info(f"Retrying with set_email_user={random_user}")
            return self.set_email_user(random_user)

    def set_email_user(self, username: str) -> str:
        """Set a specific email address username.

        Args:
            username: The local part of the email address (e.g., 'myname' -> myname@guerrillamail.com)

        Returns:
            The full email address.
        """
        logger.info(f"Setting email user: {username}")
        data = self._request({"f": "set_email_user", "email_user": username})
        self._update_state(data)

        # Some versions return addr directly
        if "addr" in data:
            self._email_addr = data["addr"]
        elif "email_addr" in data:
            self._email_addr = data["email_addr"]

        if not self._email_addr:
            raise GuerrillaMailError("Failed to set email user: no address returned")

        logger.info(f"Email set to: {self._email_addr}")
        return self._email_addr

    @property
    def email_address(self) -> Optional[str]:
        """The current email address, if any."""
        return self._email_addr

    @property
    def session_id(self) -> Optional[str]:
        """The current session token."""
        return self._sid_token

    def fetch_email(self, seq: int = 0) -> list[dict]:
        """Fetch new email messages since the given sequence number.

        Args:
            seq: Fetch messages with seq > this value. 0 means all.

        Returns:
            List of message dicts with keys: mail_id, mail_from, mail_subject,
            mail_excerpt, mail_timestamp, mail_read, content_type, mail_body, etc.
        """
        if not self._sid_token:
            raise GuerrillaMailError("No active session. Call get_email_address() first.")

        logger.info(f"Fetching emails (seq > {seq})...")
        data = self._request({"f": "fetch_email", "seq": seq}, allow_empty=True)
        messages = data.get("list", [])
        logger.info(f"Found {len(messages)} messages")
        return messages

    def get_message_body(self, mail_id: int) -> Optional[dict]:
        """Fetch the full body of a specific email message.

        Args:
            mail_id: The mail_id from a fetch_email result.

        Returns:
            Dict with full message details including mail_body.
        """
        if not self._sid_token:
            raise GuerrillaMailError("No active session. Call get_email_address() first.")

        logger.info(f"Fetching message body for mail_id={mail_id}")
        data = self._request({"f": "fetch_email", "email_id": mail_id}, allow_empty=True)
        messages = data.get("list", data.get("mail_list", []))
        if messages:
            # Return the first (and likely only) message
            msg = messages[0] if isinstance(messages, list) else messages
            return msg
        return None

    def extract_verification_links(self, messages: list[dict] = None) -> list[str]:
        """Extract verification/confirmation links from email messages.

        Args:
            messages: List of message dicts from fetch_email(). If None, fetches all messages.

        Returns:
            List of URLs found in email bodies.
        """
        if messages is None:
            messages = self.fetch_email()

        links = []
        for msg in messages:
            # Try excerpt first (faster)
            excerpt = msg.get("mail_excerpt", "")
            body = msg.get("mail_body", "")

            # If we have a body but no links in excerpt, get full body
            if not excerpt and msg.get("mail_id"):
                full_msg = self.get_message_body(msg["mail_id"])
                if full_msg:
                    body = full_msg.get("mail_body", body)
                    excerpt = full_msg.get("mail_excerpt", excerpt)

            text = f"{body} {excerpt}"
            found = re.findall(r'https?://[^\s<>"\')\]]+', text)
            links.extend(found)

        # Deduplicate
        unique_links = list(dict.fromkeys(links))
        logger.info(f"Extracted {len(unique_links)} unique links from {len(messages)} messages")
        return unique_links

    def wait_for_email(self, timeout: int = 60, poll_interval: int = 5) -> list[dict]:
        """Wait for new emails to arrive.

        Args:
            timeout: Maximum seconds to wait.
            poll_interval: Seconds between polls.

        Returns:
            List of new messages received.
        """
        logger.info(f"Waiting for email (timeout={timeout}s, poll={poll_interval}s)...")
        start = time.time()
        all_messages = []

        while time.time() - start < timeout:
            try:
                messages = self.fetch_email()
                # Check for new messages
                new_msgs = [m for m in messages if not m.get("mail_read", False)]
                if new_msgs:
                    all_messages.extend(new_msgs)
                    logger.info(f"Received {len(new_msgs)} new message(s)!")
                    return all_messages

                logger.debug("No new messages yet, waiting...")
                time.sleep(poll_interval)
            except GuerrillaMailError as e:
                logger.warning(f"Error while waiting: {e}")
                time.sleep(poll_interval)

        logger.info(f"Timed out waiting for email after {timeout}s")
        return all_messages


def test_guerrilla_mail() -> dict:
    """Test the Guerrilla Mail integration end-to-end.

    Returns a dict with test results.
    """
    logger.info("=== Testing Guerrilla Mail Integration ===")
    result = {"success": False, "email": None, "session_id": None, "errors": []}

    try:
        gm = GuerrillaMail()

        # Step 1: Get an email
        email = gm.get_email_address()
        result["email"] = email
        result["session_id"] = gm.session_id
        logger.info(f"✓ Got email address: {email}")

        if not email:
            result["errors"].append("No email returned")
            return result

        # Step 2: Verify session
        if not gm.session_id:
            result["errors"].append("No session ID")
            return result
        logger.info(f"✓ Got session ID: {gm.session_id[:20]}...")

        # Step 3: Fetch messages (should be empty for new inbox)
        messages = gm.fetch_email()
        logger.info(f"✓ Fetched messages: {len(messages)} (expected: 0)")

        # Step 4: Test extract links
        links = gm.extract_verification_links(messages)
        logger.info(f"✓ Extracted links: {len(links)} (expected: 0)")

        result["success"] = True
        result["messages_found"] = len(messages)
        logger.info("=== Guerrilla Mail Test: PASSED ===")

    except Exception as e:
        result["errors"].append(str(e))
        logger.error(f"Guerrilla Mail test failed: {e}")

    return result
