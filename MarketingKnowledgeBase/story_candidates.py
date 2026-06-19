"""Build ranked marketing story candidates from synced live context."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_DOLLAR_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
_CODE_RE = re.compile(r"\b(?:code|promo code|coupon code)\s*[:\-]?\s*([A-Z0-9]{5,})\b", re.I)
_MARKET_RE = re.compile(r"\b(?:market|resell|resale|selling|sell|sells|comps?|going)\b.{0,35}?(\$\s?\d[\d,]*(?:\.\d{2})?(?:\s?[-–]\s?\$?\s?\d[\d,]*(?:\.\d{2})?)?\+?)", re.I)
_RETAIL_RE = re.compile(r"\b(?:retail|msrp|normal(?:\s+retail)?|was|original(?:ly)?)\b.{0,30}?(\$\s?\d[\d,]*(?:\.\d{2})?)", re.I)
_PRICE_RE = re.compile(r"\b(?:price|cost|for|at|posted at|only)\b.{0,25}?(\$\s?\d[\d,]*(?:\.\d{2})?)", re.I)
_PROFIT_RE = re.compile(
    r"\b(profit|resell|resale|sold|cashout|cash out|flip|flips|roi|margin|glitch|error|kaboom|haul|cook|cooked|easy money)\b",
    re.I,
)
_ADMIN_SOURCE_BUCKETS = {"important", "important_instore", "important_trading_cards", "full_send_info"}
_FILLER_RE = re.compile(
    r"\b(info (?:will be )?provided in the morning|current points|awarded \d+ point|congratulations <@|react .{0,30}@everyone|daily reminder)\b",
    re.I,
)
_STORE_WORDS = (
    "amazon",
    "walmart",
    "target",
    "best buy",
    "gamestop",
    "microcenter",
    "amc",
    "nike",
    "vans",
    "costco",
    "sam's club",
    "five below",
    "dollar tree",
    "home depot",
    "lowes",
    "ebay",
    "stockx",
)

SOURCE_BUCKETS = (
    "success",
    "important",
    "important_instore",
    "important_trading_cards",
    "what_you_missed",
    "sneakers",
    "full_send_info",
    "full_send_monitor",
    "staff_wins",
    "daily_schedule",
    "weekly_schedule",
)
MAX_CANDIDATES = 40


def _parse_ts(value: Any) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _reaction_count(entry: Dict[str, Any]) -> int:
    total = 0
    for row in entry.get("reactions") or []:
        if isinstance(row, dict):
            total += int(row.get("count") or 0)
    return total


def _recency_score(entry: Dict[str, Any]) -> int:
    posted = _parse_ts(entry.get("posted_at"))
    if posted == datetime.min.replace(tzinfo=timezone.utc):
        return 0
    age_hours = (datetime.now(timezone.utc) - posted).total_seconds() / 3600
    if age_hours <= 12:
        return 5
    if age_hours <= 24:
        return 4
    if age_hours <= 48:
        return 3
    if age_hours <= 96:
        return 1
    return 0


def _feedback_boost(entry: Dict[str, Any], feedback_index: Dict[str, Dict[str, Any]]) -> int:
    mid = str(entry.get("message_id") or "")
    story_key = f"{entry.get('bucket')}:{mid}" if mid else ""
    row = feedback_index.get(story_key) or feedback_index.get(mid) or {}
    status = str(row.get("status") or "")
    if status == "approved":
        return 15
    if status == "rejected":
        return -100
    if status == "mixed":
        return -10
    return 0


def _score_entry(entry: Dict[str, Any], feedback_index: Optional[Dict[str, Dict[str, Any]]] = None) -> int:
    score = 0
    attachments = entry.get("attachments") or []
    embed_images = entry.get("embed_images") or []
    text = str(entry.get("text") or "")
    bucket = str(entry.get("bucket") or "")
    facts = extract_deal_facts(entry)

    if attachments:
        score += 5
    if embed_images:
        score += 4
    if _DOLLAR_RE.search(text):
        score += 5
    if facts.get("market_value") or facts.get("resale_value"):
        score += 6
    if facts.get("price_delta_pct") is not None:
        score += 5
    if facts.get("promo_code"):
        score += 2
    if facts.get("store_or_platform"):
        score += 2
    if _PROFIT_RE.search(text):
        score += 5
    if _FILLER_RE.search(text):
        score -= 12
    if len(text) >= 120:
        score += 1
    if bucket in _ADMIN_SOURCE_BUCKETS:
        score += 5
    if bucket in ("success", "full_send_info", "staff_wins"):
        score += 2
    if bucket in ("important", "important_instore", "important_trading_cards"):
        score += 1
    if bucket == "what_you_missed":
        score += 1
    reactions = _reaction_count(entry)
    if reactions:
        score += min(reactions, 8)
    score += _recency_score(entry)
    if feedback_index:
        score += _feedback_boost(entry, feedback_index)
    return score


def _headline_hints(text: str) -> List[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    hints: List[str] = []
    for ln in lines[:3]:
        upper_ratio = sum(1 for c in ln if c.isupper()) / max(len(ln), 1)
        if upper_ratio > 0.5 or "MEMBERS" in ln.upper() or "PROFIT" in ln.upper():
            hints.append(ln[:200])
    if not hints and lines:
        hints.append(lines[0][:200])
    return hints[:3]


def _extract_dollar_amounts(text: str) -> List[str]:
    return _DOLLAR_RE.findall(text)[:8]


def _money_to_float(value: str) -> Optional[float]:
    match = _DOLLAR_RE.search(str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _clean_title(text: str) -> str:
    for line in str(text or "").splitlines():
        line = re.sub(r"<a?:[^>]+>", "", line)
        line = re.sub(r"<(?:#|@|@&|&)?\d+>", "", line)
        line = re.sub(r"https?://\S+", "", line)
        line = line.replace("#", " ").replace("*", " ").replace("_", " ")
        line = re.sub(r"\s+", " ", line).strip(" -:|")
        if len(line) >= 8 and not _FILLER_RE.search(line):
            return line[:180]
    return ""


def extract_deal_facts(entry: Dict[str, Any]) -> Dict[str, Any]:
    text = str(entry.get("text") or "")
    amounts = _extract_dollar_amounts(text)
    price = next((m.group(1) for m in _PRICE_RE.finditer(text)), amounts[0] if amounts else "")
    retail = next((m.group(1) for m in _RETAIL_RE.finditer(text)), "")
    market = next((m.group(1) for m in _MARKET_RE.finditer(text)), "")
    code_match = _CODE_RE.search(text)
    hay = text.lower()
    stores = [store for store in _STORE_WORDS if store in hay]
    price_num = _money_to_float(price)
    retail_num = _money_to_float(retail)
    market_num = _money_to_float(market)
    delta_pct: Optional[int] = None
    reference = retail_num or market_num
    if price_num is not None and reference and reference > price_num:
        delta_pct = int(round(((reference - price_num) / reference) * 100))
    urgency = []
    for marker in ("limited", "few", "price error", "glitch", "free", "today", "tomorrow", "fast", "checking", "local"):
        if marker in hay:
            urgency.append(marker)
    return {
        "title": _clean_title(text),
        "amounts": amounts,
        "price": price,
        "retail_or_msrp": retail,
        "market_value": market,
        "resale_value": market,
        "price_delta_pct": delta_pct,
        "promo_code": code_match.group(1).upper() if code_match else "",
        "store_or_platform": stores[:5],
        "urgency_hints": urgency[:8],
        "has_filler": bool(_FILLER_RE.search(text)),
    }


def build_story_candidates(live_doc: Dict[str, Any]) -> Dict[str, Any]:
    buckets = live_doc.get("buckets") or {}
    raw: List[Dict[str, Any]] = []
    try:
        from MarketingKnowledgeBase.feedback import load_feedback_index

        feedback_index = load_feedback_index()
    except Exception:
        feedback_index = {}

    for bucket_name in SOURCE_BUCKETS:
        payload = buckets.get(bucket_name) or {}
        for entry in payload.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            score = _score_entry(entry, feedback_index)
            if score < 3:
                continue
            attachments = entry.get("attachments") or []
            embed_images = entry.get("embed_images") or []
            if not attachments and not embed_images and score < 5:
                continue
            mid = str(entry.get("message_id") or "")
            if not mid:
                continue
            feedback_row = feedback_index.get(f"{bucket_name}:{mid}") or feedback_index.get(mid) or {}
            candidate_row = {
                "story_id": f"{bucket_name}:{mid}",
                "message_id": mid,
                "bucket": bucket_name,
                "channel_id": entry.get("channel_id"),
                "channel_name": entry.get("channel_name"),
                "posted_at": entry.get("posted_at"),
                "author": entry.get("author"),
                "text": entry.get("text"),
                "urls": entry.get("urls") or [],
                "attachments": attachments,
                "embed_images": embed_images,
                "reactions": entry.get("reactions") or [],
                "message_link": entry.get("message_link"),
                "score": score,
                "feedback_status": feedback_row.get("status") or "",
                "dollar_amounts": _extract_dollar_amounts(str(entry.get("text") or "")),
                "headline_hints": _headline_hints(str(entry.get("text") or "")),
                "deal_facts": extract_deal_facts(entry),
            }
            try:
                from MarketingKnowledgeBase.what_you_missed_sourcing import is_bot_or_monitor_noise

                if is_bot_or_monitor_noise(candidate_row):
                    continue
            except ImportError:
                pass
            raw.append(candidate_row)

    by_id: Dict[str, Dict[str, Any]] = {}
    for item in raw:
        mid = item["message_id"]
        prev = by_id.get(mid)
        if not prev or item["score"] > prev["score"]:
            by_id[mid] = item

    ranked = sorted(by_id.values(), key=lambda x: (_parse_ts(x.get("posted_at")), x["score"]), reverse=True)
    now = datetime.now(timezone.utc).isoformat()
    return {
        "version": 1,
        "generated_at": now,
        "source_last_sync_at": live_doc.get("last_sync_at"),
        "candidates": ranked[:MAX_CANDIDATES],
    }


def find_candidate(candidates_doc: Dict[str, Any], story_id: str) -> Optional[Dict[str, Any]]:
    for item in candidates_doc.get("candidates") or []:
        if str(item.get("story_id")) == story_id:
            return item
    return None


def find_candidate_by_message_id(candidates_doc: Dict[str, Any], message_id: str) -> Optional[Dict[str, Any]]:
    for item in candidates_doc.get("candidates") or []:
        if str(item.get("message_id")) == message_id:
            return item
    return None
