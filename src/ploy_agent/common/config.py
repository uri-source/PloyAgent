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
    poly_gamma_event_slugs: str = Field(
        default="",
        alias="POLY_GAMMA_EVENT_SLUGS",
        description=(
            "Comma-separated Gamma event slugs to always ingest "
            "(e.g. spacex-or-openai-higher-ipo-closing-market-cap)"
        ),
    )
    poly_gamma_series_slugs: str = Field(
        default="",
        alias="POLY_GAMMA_SERIES_SLUGS",
        description=(
            "Comma-separated Gamma series slugs for event discovery "
            "(e.g. soccer-fifwc for all FIFA WC game moneylines)"
        ),
    )
    poly_gamma_discovery_limit: int = Field(
        default=500,
        alias="POLY_GAMMA_DISCOVERY_LIMIT",
        description="Max events fetched per tag/series per discovery pass",
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
        default="book_imbalance,cross_venue_arb,consensus",
        alias="AGENT_STRATEGIES",
        description="Comma-separated strategy ids; consensus must be last if enabled",
    )

    enrichment_enabled: bool = Field(
        default=False,
        alias="ENRICHMENT_ENABLED",
        description="When false, ploy-enrich is optional (price-only stack)",
    )

    kalshi_enabled: bool = Field(default=True, alias="KALSHI_ENABLED")
    kalshi_base_url: str = Field(
        default="https://api.elections.kalshi.com/trade-api/v2",
        alias="KALSHI_BASE_URL",
    )
    kalshi_api_key_id: str = Field(default="", alias="KALSHI_API_KEY_ID")
    kalshi_private_key_path: str = Field(default="", alias="KALSHI_PRIVATE_KEY_PATH")
    kalshi_poll_interval_sec: float = Field(default=10.0, alias="KALSHI_POLL_INTERVAL_SEC")
    kalshi_wc_game_series: str = Field(default="KXWCGAME", alias="KALSHI_WC_GAME_SERIES")
    cross_venue_map_min_confidence: float = Field(
        default=0.85, alias="CROSS_VENUE_MAP_MIN_CONFIDENCE"
    )
    cross_venue_map_review_confidence: float = Field(
        default=0.60, alias="CROSS_VENUE_MAP_REVIEW_CONFIDENCE"
    )
    cross_venue_poly_event_slug_prefix: str = Field(
        default="fifwc", alias="CROSS_VENUE_POLY_EVENT_SLUG_PREFIX"
    )

    poly_fee_rate: float = Field(default=0.02, alias="POLY_FEE_RATE")
    kalshi_fee_rate: float = Field(default=0.01, alias="KALSHI_FEE_RATE")
    cross_venue_min_edge_cents: float = Field(default=8.0, alias="CROSS_VENUE_MIN_EDGE_CENTS")
    cross_venue_max_stale_sec: float = Field(default=30.0, alias="CROSS_VENUE_MAX_STALE_SEC")
    cross_venue_min_depth: float = Field(default=500.0, alias="CROSS_VENUE_MIN_DEPTH")
    cross_venue_pairs_path: str = Field(
        default="config/cross_venue/world_cup_pairs.yaml",
        alias="CROSS_VENUE_PAIRS_PATH",
    )
    reason_candidate_max_hours: float = Field(default=6.0, alias="REASON_CANDIDATE_MAX_HOURS")

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

    auto_approve_recs: bool = Field(
        default=True, alias="AUTO_APPROVE_RECS",
        description="Auto-approve recommendations (no human gate, for paper trading / testing)",
    )
    min_edge_cents: float = Field(default=8.0, alias="MIN_EDGE_CENTS")
    entry_price_min: float = Field(
        default=0.40, alias="ENTRY_PRICE_MIN",
        description="Reject trades where market_prob < this (risk/reward floor)",
    )
    entry_price_max: float = Field(
        default=0.60, alias="ENTRY_PRICE_MAX",
        description="Reject trades where market_prob > this (risk/reward ceiling)",
    )
    min_risk_reward: float = Field(
        default=0.40, alias="MIN_RISK_REWARD",
        description="Hard floor on risk_reward_factor — reject trades below this",
    )
    rank_top_n: int = Field(default=5, alias="RANK_TOP_N")
    rank_merge_by_market: bool = Field(default=False, alias="RANK_MERGE_BY_MARKET")

    # Alert filters — picks below these thresholds are silently dropped from notifications
    alert_min_edge: float = Field(
        default=0.0, alias="ALERT_MIN_EDGE",
        description="Min abs(edge_cents) for Slack/Telegram alerts (0 = use MIN_EDGE_CENTS)",
    )
    alert_min_depth: float = Field(
        default=0.0, alias="ALERT_MIN_DEPTH",
        description="Min depth_1c for alerts (0 = no filter)",
    )
    alert_min_score: float = Field(
        default=0.0, alias="ALERT_MIN_SCORE",
        description="Min composite score for alerts (0 = no filter)",
    )

    sim_forward_interval_sec: float = Field(
        default=5.0,
        alias="SIM_FORWARD_INTERVAL_SEC",
        description="ploy-sim forward loop interval (seconds)",
    )
    sim_forward_run_hours: float = Field(
        default=336.0,
        alias="SIM_FORWARD_RUN_HOURS",
        description=(
            "Forward paper-trading auto-stops after N hours (336 = 14 days). "
            "0 = run until manual stop."
        ),
    )
    sim_replay_days: int = Field(
        default=14,
        alias="SIM_REPLAY_DAYS",
        description="Default lookback for ploy-sim replay when --from is omitted",
    )
    sim_max_hours_to_resolution: float = Field(
        default=0.0,
        alias="SIM_MAX_HOURS_TO_RESOLUTION",
        description=(
            "Paper sim: skip new entries when market end_date is unknown or farther than "
            "N hours out. 0 = disabled."
        ),
    )
    sim_forward_profiles: str = Field(
        default="e8_c70_m65",
        alias="SIM_FORWARD_PROFILES",
        description=(
            "Comma-separated sim profile ids for ploy-sim forward (e.g. e8_c70_m65). "
            "Empty = all profiles from init-profiles."
        ),
    )
    sim_edge_persistence_ticks: int = Field(
        default=3,
        alias="SIM_EDGE_PERSISTENCE_TICKS",
        description="Paper sim: require N same-direction fair_value ticks before entry (0=off)",
    )
    sim_edge_persistence_min_sec: float = Field(
        default=30.0,
        alias="SIM_EDGE_PERSISTENCE_MIN_SEC",
        description="Min span across persistence ticks (seconds)",
    )

    web_host: str = Field(default="127.0.0.1", alias="WEB_HOST")
    web_port: int = Field(default=8765, alias="WEB_PORT")

    slack_bot_token: str = Field(default="", alias="SLACK_BOT_TOKEN")
    slack_channel: str = Field(default="", alias="SLACK_CHANNEL")
    slack_signing_secret: str = Field(default="", alias="SLACK_SIGNING_SECRET")
    slack_events_port: int = Field(default=8766, alias="SLACK_EVENTS_PORT")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

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

    def sim_forward_profile_ids(self) -> list[str]:
        return [s.strip() for s in self.sim_forward_profiles.split(",") if s.strip()]

    def discovery_tag_csv(self) -> str:
        """Polymarket Gamma tag list for market discovery."""
        g = self.poly_gamma_tags.strip()
        if g:
            return g
        return self.poly_nba_tags.strip() or "nba"

    def discovery_event_slug_list(self) -> list[str]:
        return [s.strip() for s in self.poly_gamma_event_slugs.split(",") if s.strip()]

    def discovery_series_slug_list(self) -> list[str]:
        return [s.strip() for s in self.poly_gamma_series_slugs.split(",") if s.strip()]

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
