"""Configuration loaded once from the deployment environment, never exposed by API."""

from dataclasses import dataclass, field
import os
import secrets
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _url(value: str, name: str, required: bool = True) -> str:
    value = value.strip().rstrip("/")
    if not value and not required:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not contain credentials, a query or fragment")
    return value


@dataclass(frozen=True)
class Config:
    sub2api_url: str
    sub2api_admin_key: str = field(repr=False)
    admin_token: str = field(repr=False)
    public_url: str = ""
    sub2api_public_url: str = ""
    db_path: str = "/data/helper.db"
    timezone: str = "Asia/Shanghai"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = field(default="", repr=False)
    smtp_from_addr: str = ""
    smtp_tls: str = "starttls"

    @classmethod
    def from_env(cls):
        admin_token = os.environ.get("HELPER_ADMIN_TOKEN", "").strip() or secrets.token_urlsafe(32)
        if len(admin_token) < 24:
            raise ValueError("HELPER_ADMIN_TOKEN must contain at least 24 characters")
        upstream_key = os.environ.get("SUB2API_ADMIN_KEY", "").strip()
        upstream_url = _url(os.environ.get("SUB2API_URL", ""), "SUB2API_URL", False)
        if upstream_key and not upstream_url:
            raise ValueError("SUB2API_URL is required when SUB2API_ADMIN_KEY is configured")
        timezone = os.environ.get("TZ", "Asia/Shanghai")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("TZ must name an IANA time zone") from exc
        tls = os.environ.get("SMTP_TLS", "starttls").lower()
        if tls not in ("starttls", "ssl"):
            raise ValueError("SMTP_TLS must be starttls or ssl; plaintext SMTP is unsupported")
        try:
            port = int(os.environ.get("SMTP_PORT", "465" if tls == "ssl" else "587"))
        except ValueError as exc:
            raise ValueError("SMTP_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("SMTP_PORT must be between 1 and 65535")
        smtp_host = os.environ.get("SMTP_HOST", "").strip()
        smtp_from = os.environ.get("SMTP_FROM", "").strip()
        if smtp_host and not smtp_from:
            raise ValueError("SMTP_FROM is required when SMTP_HOST is configured")
        return cls(
            sub2api_url=upstream_url,
            sub2api_admin_key=upstream_key,
            admin_token=admin_token,
            public_url=_url(os.environ.get("HELPER_PUBLIC_URL", ""), "HELPER_PUBLIC_URL", False),
            sub2api_public_url=_url(os.environ.get("SUB2API_PUBLIC_URL", ""), "SUB2API_PUBLIC_URL", False),
            db_path=os.environ.get("HELPER_DB_PATH", "/data/helper.db"),
            timezone=timezone,
            smtp_host=smtp_host,
            smtp_port=port,
            smtp_user=os.environ.get("SMTP_USER", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            smtp_from_addr=smtp_from,
            smtp_tls=tls,
        )
