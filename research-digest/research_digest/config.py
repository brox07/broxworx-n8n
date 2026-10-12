import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str


@dataclass(frozen=True)
class ArxivConfig:
    categories: list[str]
    announce_types: list[str] = field(default_factory=lambda: ["new", "cross"])


@dataclass(frozen=True)
class Config:
    database_url: str
    arxiv: ArxivConfig | None
    feeds: list[FeedConfig]
    interests: str
    relevance_threshold: float
    max_age_days: int
    scorer_model: str = "@cf/cloudflare/clef-flash"
    cloudflare_account_id: str | None = None
    cloudflare_ai_token: str | None = None


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("RESEARCH_CONFIG", DEFAULT_CONFIG_PATH))
    raw = yaml.safe_load(path.read_text()) or {}

    database_url = os.environ.get("RESEARCH_DATABASE_URL")
    if not database_url:
        raise RuntimeError("RESEARCH_DATABASE_URL is not set")

    arxiv_raw = raw.get("arxiv")
    arxiv = ArxivConfig(**arxiv_raw) if arxiv_raw and arxiv_raw.get("categories") else None

    return Config(
        database_url=database_url,
        arxiv=arxiv,
        feeds=[FeedConfig(**f) for f in raw.get("feeds") or []],
        interests=(raw.get("interests") or "").strip(),
        relevance_threshold=float(raw.get("relevance_threshold", 0.7)),
        max_age_days=int(raw.get("max_age_days", 3)),
        scorer_model=(raw.get("scorer") or {}).get("model", "@cf/cloudflare/clef-flash"),
        cloudflare_account_id=os.environ.get("CLOUDFLARE_ACCOUNT_ID"),
        cloudflare_ai_token=os.environ.get("CLOUDFLARE_AI_API_TOKEN"),
    )
