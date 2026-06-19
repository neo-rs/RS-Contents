"""Pick grounded stories for #what-you-missed (hype, not monitor bots)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_BASE = Path(__file__).resolve().parent

_BOT_URL_RE = re.compile(
    r"https?://(?:zephr\.app|dash\.nikeshoebot|stockx\.com/search)[^\s\)>]*",
    re.I,
)
_ANY_URL_RE = re.compile(r"https?://\S+")
_BOT_USERNAME_RE = re.compile(
    r"(shop\.complex\.com|zephyr|nikeshoebot|restock|monitor bot|quicktask|full size run|FSR|ATC / QT)",
    re.I,
)
_MONITOR_CHANNEL_RE = re.compile(r"(shopify-filtered|monitor|filtered|restock-)", re.I)
_FILLER_RE = re.compile(
    r"\b(info (?:will be )?provided in the morning|current points|awarded \d+ point|congratulations <@|daily reminder)\b",
    re.I,
)

_DEFAULT_ELIGIBLE = (
    "full_send_info",
    "important",
    "important_instore",
    "important_trading_cards",
    "daily_schedule",
    "weekly_schedule",
    "success",
)
_DEFAULT_EXCLUDE = ("sneakers", "full_send_monitor", "monitoring", "chipotle", "staff_wins")
_BUCKET_PRIORITY = (
    "full_send_info",
    "important",
    "important_instore",
    "important_trading_cards",
    "daily_schedule",
    "weekly_schedule",
    "success",
)


def _load_config() -> Dict[str, Any]:
    path = _BASE / "config.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _sourcing_config() -> Dict[str, Any]:
    cfg = _load_config()
    return cfg.get("what_you_missed_sourcing") or {}


def _recently_posted_story_ids(limit: int = 30) -> set[str]:
    try:
        from MarketingKnowledgeBase.post_history import used_story_ids

        return set(used_story_ids(limit=max(limit, 300)))
    except Exception:
        pass

    path = _BASE / "data" / "review_posts.json"
    if not path.exists():
        return set()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    out: set[str] = set()
    for row in (doc.get("posts") or [])[:limit]:
        if not isinstance(row, dict):
            continue
        for key in ("story_id", "source_message_id"):
            val = str(row.get(key) or "").strip()
            if val:
                out.add(val)
    return out


def is_bot_or_monitor_noise(candidate: Dict[str, Any]) -> bool:
    text = str(candidate.get("text") or "")
    urls = " ".join(candidate.get("urls") or [])
    hay = f"{text}\n{urls}"
    author = candidate.get("author") or {}
    username = str(author.get("username") or "")
    display = str(author.get("display_name") or "")
    channel = str(candidate.get("channel_name") or "")

    if _BOT_URL_RE.search(hay):
        return True
    if _BOT_USERNAME_RE.search(hay) or _BOT_USERNAME_RE.search(username) or _BOT_USERNAME_RE.search(display):
        return True
    if _MONITOR_CHANNEL_RE.search(channel):
        return True
    bucket = str(candidate.get("bucket") or "")
    if bucket in ("sneakers", "full_send_monitor", "monitoring"):
        return True
    # Sneaker monitor posts are almost always bot-formatted
    if "Price:" in text and "Stock:" in text and "ATC" in text:
        return True
    if _FILLER_RE.search(text) and not (candidate.get("attachments") or candidate.get("embed_images")):
        return True
    return False


def _score_for_wym(candidate: Dict[str, Any]) -> int:
    score = int(candidate.get("score") or 0)
    bucket = str(candidate.get("bucket") or "")
    text = str(candidate.get("text") or "").upper()
    facts = candidate.get("deal_facts") or {}

    boosts = _sourcing_config().get("bucket_score_boost") or {
        "staff_wins": 12,
        "full_send_info": 10,
        "important": 8,
        "important_instore": 8,
        "important_trading_cards": 8,
        "daily_schedule": 6,
        "weekly_schedule": 6,
        "success": 5,
    }
    score += int(boosts.get(bucket) or 0)

    if candidate.get("attachments"):
        score += 3
    if candidate.get("embed_images"):
        score += 2
    if facts.get("price_delta_pct") is not None:
        score += 8
    if facts.get("market_value") or facts.get("resale_value"):
        score += 6
    if facts.get("price") and (facts.get("retail_or_msrp") or facts.get("market_value")):
        score += 5
    if facts.get("promo_code"):
        score += 2
    if facts.get("store_or_platform"):
        score += 2
    if facts.get("has_filler"):
        score -= 15
    if "WIN OF THE DAY" in text or "WIN" in text[:40]:
        score += 8
    if "$" in text:
        score += 2
    status = str(candidate.get("feedback_status") or "")
    if status == "approved":
        score += 15
    elif status == "rejected":
        score -= 100
    elif status == "mixed":
        score -= 10
    if is_bot_or_monitor_noise(candidate):
        score -= 50
    return score


def filter_eligible_candidates(candidates_doc: Dict[str, Any], *, exclude_posted: bool = True) -> List[Dict[str, Any]]:
    src = _sourcing_config()
    eligible = tuple(src.get("eligible_buckets") or _DEFAULT_ELIGIBLE)
    exclude = set(src.get("exclude_buckets") or _DEFAULT_EXCLUDE)
    posted = _recently_posted_story_ids() if exclude_posted else set()

    rows: List[Dict[str, Any]] = []
    for item in candidates_doc.get("candidates") or []:
        bucket = str(item.get("bucket") or "")
        story_id = str(item.get("story_id") or "")
        message_id = str(item.get("message_id") or "")
        if story_id in posted or message_id in posted:
            continue
        if bucket in exclude:
            continue
        if bucket not in eligible:
            continue
        if is_bot_or_monitor_noise(item):
            continue
        rows.append({**item, "wym_score": _score_for_wym(item)})

    rows.sort(
        key=lambda x: (x.get("wym_score", 0), str(x.get("posted_at") or "")),
        reverse=True,
    )
    return rows


def pick_what_you_missed_story(
    candidates_doc: Dict[str, Any],
    *,
    story_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Returns (candidate, mode).
    mode: primary | wins_fallback | none
    """
    if story_id:
        from MarketingKnowledgeBase.story_candidates import find_candidate

        cand = find_candidate(candidates_doc, story_id)
        if cand and not is_bot_or_monitor_noise(cand):
            return cand, "primary"
        if cand:
            raise ValueError(f"story_id {story_id} is bot/monitor noise — not valid for what-you-missed")

    src = _sourcing_config()
    eligible = filter_eligible_candidates(candidates_doc, exclude_posted=True)
    if eligible:
        return eligible[0], "primary"

    # Wins fallback: staff wins channel posts when no admin deal posts qualify
    wins_bucket = str(src.get("wins_fallback_bucket") or "staff_wins")
    for item in candidates_doc.get("candidates") or []:
        if str(item.get("bucket")) == wins_bucket:
            return item, "wins_fallback"

    return None, "none"
