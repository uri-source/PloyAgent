from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/ploy_agent",
        alias="DATABASE_URL",
    )

    gamma_base_url: str = Field(default="https://gamma-api.polymarket.com", alias="GAMMA_BASE_URL")
    clob_base_url: str = Field(default="https://clob.polymarket.com", alias="CLOB_BASE_URL")
    poly_ws_url: str = Field(
        default="wss://ws-subscriptions-clob.polymarket.com/ws/market",
        alias="POLY_WS_URL",
    )
    poly_gamma_tags: str = Field(
        default="",
        alias="POLY_GAMMA_TAGS",
        description="Comma-separated Gamma tag slugs or ids; if empty, POLY_NBA_TAGS is used",
    )
    poly_nba_tags: str = Field(
        default="nba",
        alias="POLY_NBA_TAGS",
        description="Deprecated alias for discovery tags when POLY_GAMMA_TAGS is unset",
    )

    enrichment_espn_leagues: str = Field(
        default="nba",
        alias="ENRICHMENT_ESPN_LEAGUES",
        description="Comma-separated league keys for ESPN scoreboards (e.g. nba,mlb)",
    )
    enrichment_odds_leagues: str = Field(
        default="basketball_nba",
        alias="ENRICHMENT_ODDS_LEAGUES",
        description="Comma-separated The Odds API sport keys for scores (e.g. basketball_nba,baseball_mlb)",
    )
    baseline_model_categories: str = Field(
        default="nba",
        alias="BASELINE_MODEL_CATEGORIES",
        description="Comma-separated market categories (Gamma) where baseline/player_adjust run",
    )

    sports_provider: str = Field(default="espn", alias="SPORTS_PROVIDER")
    odds_api_key: str = Field(default="", alias="ODDS_API_KEY")
    odds_api_base: str = Field(default="https://api.the-odds-api.com/v4", alias="ODDS_API_BASE")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL")

    agent_strategies: str = Field(
        default="baseline_model",
        alias="AGENT_STRATEGIES",
        description="Comma-separated strategy ids",
    )

    stale_quote_window_sec: float = Field(default=120.0, alias="STALE_QUOTE_WINDOW_SEC")
    stale_quote_score_swing: int = Field(default=10, alias="STALE_QUOTE_SCORE_SWING")
    stale_quote_price_move: float = Field(default=0.015, alias="STALE_QUOTE_PRICE_MOVE")

    sportsbook_sharp_weights: str = Field(
        default="pinnacle:3,circa:3,draftkings:1,fanduel:1",
        alias="SPORTSBOOK_SHARP_WEIGHTS",
    )
    sportsbook_regions: str = Field(default="us,eu", alias="SPORTSBOOK_REGIONS")

    cross_market_sum_deviation: float = Field(default=0.04, alias="CROSS_MARKET_SUM_DEVIATION")

    behavior_overreaction_mult: float = Field(default=2.5, alias="BEHAVIOR_OVERREACTION_MULT")
    behavior_price_window_sec: float = Field(default=45.0, alias="BEHAVIOR_PRICE_WINDOW_SEC")

    player_adjust_scale: float = Field(default=0.004, alias="PLAYER_ADJUST_SCALE")

    min_edge_cents: float = Field(default=3.0, alias="MIN_EDGE_CENTS")
    rank_top_n: int = Field(default=5, alias="RANK_TOP_N")
    rank_merge_by_market: bool = Field(default=False, alias="RANK_MERGE_BY_MARKET")

    web_host: str = Field(default="127.0.0.1", alias="WEB_HOST")
    web_port: int = Field(default=8765, alias="WEB_PORT")

    slack_bot_token: str = Field(default="", alias="SLACK_BOT_TOKEN")
    slack_channel: str = Field(default="", alias="SLACK_CHANNEL")
    slack_events_port: int = Field(default=8766, alias="SLACK_EVENTS_PORT")

    log_json: bool = Field(default=False, alias="LOG_JSON")

    agent_log_file: Path = Field(
        default=Path("artifacts/agent.log"),
        alias="AGENT_LOG_FILE",
        description="Append-only shared log (ingest/enrich/reason tail this path for the dashboard)",
    )

    @field_validator("agent_strategies", mode="before")
    @classmethod
    def _strip_strategies(cls, v: object) -> object:
        if isinstance(v, str):
            return ",".join(s.strip() for s in v.split(",") if s.strip())
        return v

    def strategy_ids(self) -> list[str]:
        return [s.strip() for s in self.agent_strategies.split(",") if s.strip()]

    def discovery_tag_csv(self) -> str:
        """Polymarket Gamma tag list for market discovery."""
        g = self.poly_gamma_tags.strip()
        if g:
            return g
        return self.poly_nba_tags.strip() or "nba"

    def enrichment_espn_league_keys(self) -> list[str]:
        keys = [x.strip().lower() for x in self.enrichment_espn_leagues.split(",") if x.strip()]
        return keys or ["nba"]

    def enrichment_odds_sport_keys(self) -> list[str]:
        keys = [x.strip().lower() for x in self.enrichment_odds_leagues.split(",") if x.strip()]
        return keys or ["basketball_nba"]

    def baseline_model_category_set(self) -> frozenset[str]:
        return frozenset(
            x.strip().lower() for x in self.baseline_model_categories.split(",") if x.strip()
        ) or frozenset({"nba"})

    def sharp_book_weights(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for part in self.sportsbook_sharp_weights.split(","):
            part = part.strip().lower()
            if not part or ":" not in part:
                continue
            k, _, v = part.partition(":")
            try:
                out[k.strip()] = float(v.strip())
            except ValueError:
                continue
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
