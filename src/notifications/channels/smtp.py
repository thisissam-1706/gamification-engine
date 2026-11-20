"""Real SMTP email adapter (used with a fake inbox such as Ethereal in the demo).

Sends the proposal as a plain-text email over SMTP with STARTTLS (or implicit TLS). Network
and server-side temporary failures raise ``TransientProviderError`` so the dispatcher retries
and then dead-letters them; a refused login, sender or recipient raises
``PermanentProviderError``. After a successful send, ``last_message_id`` holds the server's
message id and ``last_preview_url`` a link to view it (Ethereal returns ``MSGID=...``).
"""

from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import re
import smtplib
import socket
from typing import Any

from src.notifications.channels.base import (
    BaseChannelAdapter,
    PermanentProviderError,
    TransientProviderError,
)
from src.notifications.models import NotificationProposal

_MSGID = re.compile(rb"MSGID=([A-Za-z0-9._\-]+)")


@dataclass(frozen=True)
class SmtpAccount:
    host: str
    port: int
    user: str
    password: str
    sender: str
    secure: bool = False  # True: implicit TLS (port 465); False: STARTTLS (port 587)
    starttls: bool = True  # False for a local capture server such as Mailpit (plain SMTP, port 1025)
    web_url: str | None = None  # where the inbox can be read, e.g. https://ethereal.email
    preview_base: str | None = None  # e.g. https://ethereal.email/message/


class SmtpEmailAdapter(BaseChannelAdapter):
    """Delivers email proposals through a real SMTP server."""

    def __init__(
        self,
        account: SmtpAccount,
        timeout: float = 10.0,
        smtp_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.account = account
        self.timeout = timeout
        self._factory = smtp_factory or (smtplib.SMTP_SSL if account.secure else smtplib.SMTP)
        self.last_message_id: str | None = None
        self.last_preview_url: str | None = None

    def build_message(self, proposal: NotificationProposal) -> EmailMessage:
        payload = proposal.payload
        msg = EmailMessage()
        msg["From"] = self.account.sender
        msg["To"] = str(payload["to"])
        msg["Subject"] = str(payload.get("title") or "Streakwise")
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain="streakwise.demo")
        msg["X-Proposal-Id"] = proposal.proposal_id
        msg["X-Category"] = proposal.category.value
        msg.set_content(
            f"{payload.get('body', '')}\n\n-- \nStreakwise demo notification "
            f"({proposal.category.value}, proposal {proposal.proposal_id})\n"
        )
        return msg

    def send(self, proposal: NotificationProposal) -> bool:
        recipient = proposal.payload.get("to")
        if not recipient:
            raise PermanentProviderError("Email proposal has no recipient (payload['to'])")
        message = self.build_message(proposal)
        try:
            server = self._factory(self.account.host, self.account.port, timeout=self.timeout)
        except (OSError, socket.timeout, smtplib.SMTPException) as exc:
            raise TransientProviderError(f"SMTP connect to {self.account.host} failed: {exc}") from exc
        try:
            server.ehlo()
            if not self.account.secure and self.account.starttls:
                server.starttls()
                server.ehlo()
            if self.account.user:  # a local capture server needs no login
                server.login(self.account.user, self.account.password)
            code, resp = server.mail(self.account.sender)
            if code >= 400:
                raise smtplib.SMTPSenderRefused(code, resp, self.account.sender)
            code, resp = server.rcpt(str(recipient))
            if code >= 400:
                raise smtplib.SMTPRecipientsRefused({str(recipient): (code, resp)})
            code, resp = server.data(message.as_bytes())
            if code >= 400:
                raise smtplib.SMTPDataError(code, resp)
        except smtplib.SMTPAuthenticationError as exc:
            raise PermanentProviderError(f"SMTP login refused: {exc}") from exc
        except (smtplib.SMTPSenderRefused, smtplib.SMTPRecipientsRefused) as exc:
            raise PermanentProviderError(f"SMTP refused the message: {exc}") from exc
        except smtplib.SMTPResponseException as exc:
            if 400 <= exc.smtp_code < 500:
                raise TransientProviderError(f"SMTP temporary failure {exc.smtp_code}") from exc
            raise PermanentProviderError(f"SMTP failure {exc.smtp_code}: {exc.smtp_error!r}") from exc
        except (OSError, socket.timeout, smtplib.SMTPException) as exc:
            raise TransientProviderError(f"SMTP error: {exc}") from exc
        finally:
            try:
                server.quit()
            except Exception:
                pass
        found = _MSGID.search(resp or b"")
        self.last_message_id = found.group(1).decode() if found else None
        self.last_preview_url = (
            self.account.preview_base + self.last_message_id
            if found and self.account.preview_base
            else None
        )
        return True
