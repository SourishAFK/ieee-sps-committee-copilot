"""Environment-backed settings.

Everything here has a working default so the app boots with no .env at all.
Keys only unlock extra behaviour (LLM feedback, Telegram/email push).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


@dataclass
class Chapter:
    """Who we are. Drives fit scoring and the voice of outreach letters."""

    name: str = field(default_factory=lambda: _str("CHAPTER_NAME", "IEEE SPS Student Branch Chapter"))
    institution: str = field(default_factory=lambda: _str("INSTITUTION", "Your Institute"))
    city: str = field(default_factory=lambda: _str("CITY", "Your City"))
    country: str = field(default_factory=lambda: _str("COUNTRY", "India"))
    region: int = field(default_factory=lambda: _int("IEEE_REGION", 10))
    section: str = field(default_factory=lambda: _str("IEEE_SECTION", "Your IEEE Section"))
    chair_name: str = field(default_factory=lambda: _str("CHAIR_NAME", "Chapter Chair"))
    chair_email: str = field(default_factory=lambda: _str("CHAIR_EMAIL", ""))
    venue_capacity: int = field(default_factory=lambda: _int("VENUE_CAPACITY", 500))

    @property
    def signature(self) -> str:
        bits = [self.chair_name, f"Chair, {self.name}", self.institution]
        if self.chair_email:
            bits.append(self.chair_email)
        return "\n".join(b for b in bits if b)


@dataclass
class LLMConfig:
    provider: str = field(default_factory=lambda: _str("LLM_PROVIDER", "gemini").lower())
    gemini_key: str = field(default_factory=lambda: _str("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _str("GEMINI_MODEL", "gemini-flash-latest"))
    gemini_max_rpm: int = field(default_factory=lambda: _int("GEMINI_MAX_RPM", 5))
    gemini_max_retries: int = field(default_factory=lambda: _int("GEMINI_MAX_RETRIES", 1))
    anthropic_key: str = field(default_factory=lambda: _str("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _str("CLAUDE_MODEL", "claude-sonnet-5"))

    @property
    def enabled(self) -> bool:
        if self.provider == "gemini":
            return bool(self.gemini_key)
        if self.provider == "claude":
            return bool(self.anthropic_key)
        return False


@dataclass
class NotifyConfig:
    telegram_token: str = field(default_factory=lambda: _str("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _str("TELEGRAM_CHAT_ID"))
    smtp_host: str = field(default_factory=lambda: _str("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: _str("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _str("SMTP_PASSWORD"))
    digest_to: list[str] = field(
        default_factory=lambda: [a.strip() for a in _str("DIGEST_TO").split(",") if a.strip()]
    )

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    @property
    def email_ready(self) -> bool:
        return bool(self.smtp_user and self.smtp_password and self.digest_to)


@dataclass
class Settings:
    chapter: Chapter = field(default_factory=Chapter)
    llm: LLMConfig = field(default_factory=LLMConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    db_file: str = field(default_factory=lambda: _str("DB_FILE", "sps_copilot.db"))
    # Off when a hosted UI sleeps between visits and a CI cron does the crawling
    # instead - otherwise the schedule silently never fires and nobody notices.
    enable_scheduler: bool = field(
        default_factory=lambda: _str("ENABLE_SCHEDULER", "1").lower() not in ("0", "false", "no")
    )
    app_password: str = field(default_factory=lambda: _str("APP_PASSWORD"))
    crawl_hour: int = field(default_factory=lambda: _int("CRAWL_HOUR", 7))
    digest_day: int = field(default_factory=lambda: _int("DIGEST_DAY", 0))
    digest_hour: int = field(default_factory=lambda: _int("DIGEST_HOUR", 9))
    alert_threshold: int = field(default_factory=lambda: _int("ALERT_THRESHOLD", 70))

    @property
    def db_path(self) -> Path:
        p = Path(self.db_file)
        return p if p.is_absolute() else ROOT / p


settings = Settings()
