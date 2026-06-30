from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_POLY_WC_SLUG_RE = re.compile(r"^fifwc-([a-z]+)-([a-z]+)-(\d{4}-\d{2}-\d{2})$")
_DRAW_RE = re.compile(r"end in a draw|ends in a draw|match end in a tie", re.I)
_WIN_ON_DATE_RE = re.compile(r"^will (.+?) win on \d{4}-\d{2}-\d{2}", re.I)
_VS_TITLE_RE = re.compile(r"^(.+?)\s+vs\.?\s+(.+?)\s+winner", re.I)
_KALSHI_TIE_SUFFIX = "-TIE"


@dataclass(frozen=True)
class PolyMoneyline:
    market_id: str
    question: str
    event_slug: str
    team_a: str
    team_b: str
    kickoff_date: date
    outcome: str  # team_a | team_b | draw


@dataclass(frozen=True)
class KalshiOutcome:
    ticker: str
    event_ticker: str
    title: str
    yes_sub_title: str
    kickoff: datetime | None
    team_a: str
    team_b: str
    outcome: str  # team_a | team_b | draw


@dataclass(frozen=True)
class MatchedPair:
    pair_id: str
    label: str
    poly_market_id: str
    kalshi_ticker: str
    confidence: float
    active: bool
    poly_event_slug: str
    kalshi_event_ticker: str
    review_notes: str | None


def load_team_aliases(path: Path | None = None) -> dict[str, str]:
    p = path or Path("config/cross_venue/team_aliases.yaml")
    if not p.is_file():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    items = raw.get("aliases") or {}
    out: dict[str, str] = {}
    if isinstance(items, dict):
        for k, v in items.items():
            out[str(k).strip().lower()] = str(v).strip().lower()
    return out


def normalize_team(name_or_code: str, aliases: dict[str, str]) -> str:
    s = name_or_code.strip().lower()
    if not s:
        return s
    if s in aliases:
        return aliases[s]
    return s


def is_poly_wc_game_slug(event_slug: str | None) -> bool:
    if not event_slug:
        return False
    return _POLY_WC_SLUG_RE.match(event_slug.strip()) is not None


def parse_poly_slug(event_slug: str, aliases: dict[str, str]) -> tuple[str, str, date] | None:
    m = _POLY_WC_SLUG_RE.match(event_slug.strip())
    if not m:
        return None
    code_a, code_b, ds = m.group(1), m.group(2), m.group(3)
    try:
        kickoff = date.fromisoformat(ds)
    except ValueError:
        return None
    team_a = normalize_team(code_a, aliases)
    team_b = normalize_team(code_b, aliases)
    return team_a, team_b, kickoff


def parse_poly_moneyline(
    *,
    market_id: str,
    question: str | None,
    event_slug: str,
    aliases: dict[str, str],
) -> PolyMoneyline | None:
    if not is_poly_wc_game_slug(event_slug):
        return None
    if not question:
        return None
    q = question.strip()
    if "-more-markets" in event_slug:
        return None
    parsed = parse_poly_slug(event_slug, aliases)
    if parsed is None:
        return None
    team_a, team_b, kickoff = parsed

    if _DRAW_RE.search(q):
        return PolyMoneyline(
            market_id=market_id,
            question=q,
            event_slug=event_slug,
            team_a=team_a,
            team_b=team_b,
            kickoff_date=kickoff,
            outcome="draw",
        )

    wm = _WIN_ON_DATE_RE.match(q)
    if not wm:
        return None
    winner_raw = wm.group(1).strip().lower()
    winner = normalize_team(winner_raw, aliases)
    if winner == team_a:
        outcome = "team_a"
    elif winner == team_b:
        outcome = "team_b"
    else:
        # fuzzy: "Jordan" in question
        if winner in team_a or team_a in winner:
            outcome = "team_a"
        elif winner in team_b or team_b in winner:
            outcome = "team_b"
        else:
            return None

    return PolyMoneyline(
        market_id=market_id,
        question=q,
        event_slug=event_slug,
        team_a=team_a,
        team_b=team_b,
        kickoff_date=kickoff,
        outcome=outcome,
    )


def _parse_kalshi_kickoff(market: dict[str, Any]) -> datetime | None:
    for key in ("occurrence_datetime", "expected_expiration_time", "close_time"):
        raw = market.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def parse_kalshi_outcome(market: dict[str, Any], aliases: dict[str, str]) -> KalshiOutcome | None:
    ticker = str(market.get("ticker") or "")
    event_ticker = str(market.get("event_ticker") or "")
    title = str(market.get("title") or "")
    yes_sub = str(market.get("yes_sub_title") or market.get("no_sub_title") or "")
    if not ticker or not event_ticker:
        return None

    vm = _VS_TITLE_RE.match(title)
    if not vm:
        return None
    team_a = normalize_team(vm.group(1), aliases)
    team_b = normalize_team(vm.group(2), aliases)

    sub_lower = yes_sub.lower()
    if "tie" in sub_lower or ticker.endswith(_KALSHI_TIE_SUFFIX):
        outcome = "draw"
    else:
        cand = normalize_team(yes_sub.split(":")[-1].strip(), aliases)
        if cand == team_a or cand in team_a or team_a in cand:
            outcome = "team_a"
        elif cand == team_b or cand in team_b or team_b in cand:
            outcome = "team_b"
        else:
            # ticker suffix e.g. -JOR -ARG
            suffix = ticker.rsplit("-", 1)[-1].lower()
            sa = normalize_team(suffix, aliases)
            if sa == team_a or sa in team_a:
                outcome = "team_a"
            elif sa == team_b or sa in team_b:
                outcome = "team_b"
            else:
                return None

    return KalshiOutcome(
        ticker=ticker,
        event_ticker=event_ticker,
        title=title,
        yes_sub_title=yes_sub,
        kickoff=_parse_kalshi_kickoff(market),
        team_a=team_a,
        team_b=team_b,
        outcome=outcome,
    )


def _team_pair_key(a: str, b: str) -> frozenset[str]:
    return frozenset({a, b})


def _date_match(poly_d: date, kalshi_dt: datetime | None, *, tolerance_hours: float = 36.0) -> bool:
    if kalshi_dt is None:
        return True
    k = kalshi_dt.date()
    if poly_d == k:
        return True
    delta_h = abs((datetime.combine(poly_d, datetime.min.time(), tzinfo=timezone.utc)
                   - kalshi_dt).total_seconds()) / 3600.0
    return delta_h <= tolerance_hours


def score_match(
    poly: PolyMoneyline,
    kalshi: KalshiOutcome,
    *,
    slug_codes_match: bool,
) -> float:
    score = 0.0
    if _team_pair_key(poly.team_a, poly.team_b) != _team_pair_key(kalshi.team_a, kalshi.team_b):
        return 0.0
    score += 0.35  # teams match
    if poly.kickoff_date and _date_match(poly.kickoff_date, kalshi.kickoff):
        score += 0.35
    elif poly.kickoff_date and kalshi.kickoff:
        delta_h = abs(
            (
                datetime.combine(poly.kickoff_date, datetime.min.time(), tzinfo=timezone.utc)
                - kalshi.kickoff
            ).total_seconds()
        ) / 3600.0
        if delta_h > 24:
            score -= 0.30
    if slug_codes_match:
        score += 0.15
    if poly.outcome == kalshi.outcome:
        score += 0.15
    else:
        return 0.0
    return max(0.0, min(1.0, score))


def slug_codes_match_event(poly: PolyMoneyline, kalshi: KalshiOutcome, aliases: dict[str, str]) -> bool:
    m = _POLY_WC_SLUG_RE.match(poly.event_slug)
    if not m:
        return False
    code_a, code_b = m.group(1).lower(), m.group(2).lower()
    et = kalshi.event_ticker.upper()
    return code_a.upper() in et and code_b.upper() in et


def match_wc_game_pairs(
    poly_rows: list[dict[str, Any]],
    kalshi_markets: list[dict[str, Any]],
    aliases: dict[str, str],
    *,
    min_confidence: float,
    review_confidence: float,
) -> list[MatchedPair]:
    poly_ml: list[PolyMoneyline] = []
    for row in poly_rows:
        pm = parse_poly_moneyline(
            market_id=str(row["id"]),
            question=row.get("question"),
            event_slug=str(row.get("event_slug") or ""),
            aliases=aliases,
        )
        if pm:
            poly_ml.append(pm)

    kalshi_by_event: dict[str, list[KalshiOutcome]] = {}
    for km in kalshi_markets:
        ko = parse_kalshi_outcome(km, aliases)
        if ko:
            kalshi_by_event.setdefault(ko.event_ticker, []).append(ko)

    results: list[MatchedPair] = []
    used_poly: set[str] = set()
    used_kalshi: set[str] = set()

    for pm in poly_ml:
        best: tuple[float, KalshiOutcome | None] = (0.0, None)
        for outcomes in kalshi_by_event.values():
            for ko in outcomes:
                if _team_pair_key(pm.team_a, pm.team_b) != _team_pair_key(ko.team_a, ko.team_b):
                    continue
                codes_ok = slug_codes_match_event(pm, ko, aliases)
                conf = score_match(pm, ko, slug_codes_match=codes_ok)
                if conf > best[0]:
                    best = (conf, ko)
        conf, ko = best
        if ko is None or conf < review_confidence:
            continue
        if pm.market_id in used_poly or ko.ticker in used_kalshi:
            continue

        active = conf >= min_confidence
        notes = None if active else f"low confidence {conf:.2f}"
        pair_id = (
            f"wc26-{pm.team_a[:3]}-{pm.team_b[:3]}-{pm.kickoff_date.isoformat()}-"
            f"{pm.outcome.replace('team_', 't')}"
        )
        label = f"{pm.question[:60]} ↔ {ko.yes_sub_title}"
        results.append(
            MatchedPair(
                pair_id=pair_id,
                label=label,
                poly_market_id=pm.market_id,
                kalshi_ticker=ko.ticker,
                confidence=round(conf, 3),
                active=active,
                poly_event_slug=pm.event_slug,
                kalshi_event_ticker=ko.event_ticker,
                review_notes=notes,
            )
        )
        used_poly.add(pm.market_id)
        used_kalshi.add(ko.ticker)

    return results
