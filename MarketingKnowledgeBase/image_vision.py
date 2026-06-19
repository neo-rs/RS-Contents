"""OpenAI vision summaries for success-channel proof screenshots."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from MarketingKnowledgeBase.model_router import resolve_model
from MarketingKnowledgeBase.secrets import openai_api_key


def describe_proof_images(image_urls: List[str], *, context: str = "") -> Optional[str]:
    """Return plain-English description of proof images (OpenAI vision)."""
    urls = [str(u or "").strip() for u in image_urls if str(u or "").strip()]
    if not urls:
        return None

    api_key = openai_api_key()
    if not api_key:
        return None

    routing = resolve_model(task="generate_marketing_copy", grounding_chars=len(context))
    model = routing.get("model") or "gpt-4o"

    content: List[Dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Describe this Discord member success / profit screenshot for marketing copy. "
                "Only state what you can see (product, store, prices, profit, app UI). "
                "Do not invent numbers. Short bullets.\n"
                f"{context}".strip()
            ),
        }
    ]
    for url in urls[:2]:
        content.append({"type": "image_url", "image_url": {"url": url}})

    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 400,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return None

    choices = body.get("choices") or []
    if not choices:
        return None
    text = choices[0].get("message", {}).get("content")
    return str(text).strip() if text else None
