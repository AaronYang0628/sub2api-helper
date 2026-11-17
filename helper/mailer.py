"""SMTP with verified TLS. No upstream SMTP credentials are read or exported."""

import asyncio
from email.message import EmailMessage
from email.utils import parseaddr
import smtplib
import ssl


class MailDeliveryError(RuntimeError):
    def __init__(self, retryable=False):
        super().__init__("SMTP delivery failed" if retryable else "SMTP delivery outcome is uncertain; review before resending")
        self.retryable = retryable


class Mailer:
    def __init__(self, config):
        self.config = config

    @property
    def configured(self):
        return bool(self.config.smtp_host and self.config.smtp_from_addr)

    async def send(self, to, subject, body):
        if not self.configured:
            raise RuntimeError("SMTP is not configured")
        if any(c in to + subject for c in "\r\n") or not parseaddr(to)[1]:
            raise ValueError("Invalid recipient or subject")
        message = EmailMessage()
        message["From"] = self.config.smtp_from_addr
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        await asyncio.to_thread(self._send, message)

    def _send(self, message):
        cfg = self.config
        context = ssl.create_default_context()
        sending = False
        try:
            if cfg.smtp_tls == "ssl":
                connection = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=30, context=context)
            else:
                connection = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30)
            with connection as smtp:
                if cfg.smtp_tls == "starttls":
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if cfg.smtp_user:
                    smtp.login(cfg.smtp_user, cfg.smtp_password)
                sending = True
                rejected = smtp.send_message(message)
                if rejected:
                    raise smtplib.SMTPRecipientsRefused(rejected)
        except Exception as exc:
            # A timeout/disconnect after DATA can mean the server accepted the mail.
            # Never silently retry that outcome and produce duplicate notifications.
            retryable = not sending or isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError))
            raise MailDeliveryError(retryable) from exc
