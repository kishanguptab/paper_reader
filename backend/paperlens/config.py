"""Settings from environment / .env. Nothing secret is ever logged."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env",
                      Path(__file__).resolve().parents[3] / ".env"):
        if candidate.is_file():
            for line in candidate.read_text(errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break


_load_dotenv()


@dataclass
class Settings:
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    papers_dir: Path = field(default_factory=lambda: Path(os.getenv("PAPERLENS_PAPERS_DIR", "papers")).expanduser())
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("PAPERLENS_DATA_DIR", "data")).expanduser())
    # Cheap, fast model for bulk paragraph explanations; stronger model for synthesis.
    fast_model: str = field(default_factory=lambda: os.getenv("PAPERLENS_FAST_MODEL", "claude-haiku-5-5"))
    smart_model: str = field(default_factory=lambda: os.getenv("PAPERLENS_SMART_MODEL", "claude-sonnet-5-5"))
    chat_model: str = field(default_factory=lambda: os.getenv("PAPERLENS_CHAT_MODEL", "claude-sonnet-5-5"))
    budget_usd: float = field(default_factory=lambda: float(os.getenv("PAPERLENS_BUDGET_USD", "20")))
    paragraph_batch: int = field(default_factory=lambda: int(os.getenv("PAPERLENS_PARAGRAPH_BATCH", "10")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "paperlens.db"


settings = Settings()
