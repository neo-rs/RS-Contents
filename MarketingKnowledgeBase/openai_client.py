"""Shared OpenAI Responses API client for RS content automation."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from MarketingKnowledgeBase.secrets import openai_api_key

RESPONSES_URL = "https://api.openai.com/v1/responses"
CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"


@dataclass
class OpenAIResult:
    text: str
    model: str
    usage: Dict[str, Any]
    raw: Dict[str, Any]
    endpoint: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text.strip())


@dataclass
class OpenAIToolStep:
    text: str
    model: str
    usage: Dict[str, Any]
    raw: Dict[str, Any]
    endpoint: str
    response_id: str = ""
    tool_calls: List[Dict[str, Any]] | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _extract_response_text(body: Dict[str, Any]) -> str:
    text = body.get("output_text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    parts: List[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            val = content.get("text")
            if isinstance(val, str):
                parts.append(val)
    return "\n".join(p.strip() for p in parts if p and p.strip()).strip()


def _extract_tool_calls(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"function_call", "tool_call"}:
            raw_args = item.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except Exception:
                args = {"_raw_arguments": raw_args}
            calls.append(
                {
                    "call_id": item.get("call_id") or item.get("id") or "",
                    "name": item.get("name") or "",
                    "arguments": args,
                    "raw": item,
                }
            )
    return calls


def _extract_chat_text(body: Dict[str, Any]) -> str:
    choices = body.get("choices") or []
    if not choices:
        return ""
    content = choices[0].get("message", {}).get("content")
    return str(content or "").strip()


class OpenAIResponsesClient:
    """Small urllib-based client so the repo does not require a new SDK dependency."""

    def __init__(self, *, api_key: Optional[str] = None, timeout_s: int = 120) -> None:
        self.api_key = api_key or openai_api_key()
        self.timeout_s = timeout_s

    def _post(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key:
            raise RuntimeError(
                "Missing OpenAI API key. Create MarketingKnowledgeBase/config.secrets.json "
                "with openai_api_key, or set OPENAI_API_KEY."
            )
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def responses_text(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        reasoning_effort: str = "medium",
        text_format: Optional[Dict[str, Any]] = None,
        image_urls: Optional[List[str]] = None,
        store: bool = False,
        max_output_tokens: Optional[int] = None,
        fallback_to_chat: bool = True,
    ) -> OpenAIResult:
        content: Any
        urls = [str(u or "").strip() for u in (image_urls or []) if str(u or "").strip()]
        if urls:
            content = [{"type": "input_text", "text": input_text}]
            for url in urls:
                content.append({"type": "input_image", "image_url": url})
            input_payload: Any = [{"role": "user", "content": content}]
        else:
            input_payload = input_text

        payload: Dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_payload,
            "store": bool(store),
        }
        if str(model).lower().startswith("gpt-5"):
            payload["reasoning"] = {"effort": reasoning_effort}
        if text_format:
            payload["text"] = {"format": text_format}
        if max_output_tokens:
            payload["max_output_tokens"] = int(max_output_tokens)

        last_error = ""
        for attempt in range(1, 3):
            try:
                body = self._post(RESPONSES_URL, payload)
                return OpenAIResult(
                    text=_extract_response_text(body),
                    model=model,
                    usage=body.get("usage") or {},
                    raw=body,
                    endpoint="responses",
                )
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = f"OpenAI Responses API error {exc.code}: {detail}"
                if exc.code not in (408, 409, 429, 500, 502, 503, 504):
                    break
            except Exception as exc:
                last_error = f"OpenAI Responses API error: {type(exc).__name__}: {exc}"
            time.sleep(0.75 * attempt)

        if fallback_to_chat:
            return self.chat_text(
                model=model,
                system=instructions,
                user=input_text,
                image_urls=urls,
                max_tokens=max_output_tokens,
                prior_error=last_error,
            )

        return OpenAIResult(text="", model=model, usage={}, raw={}, endpoint="responses", error=last_error)

    def chat_text(
        self,
        *,
        model: str,
        system: str,
        user: str,
        image_urls: Optional[List[str]] = None,
        max_tokens: Optional[int] = None,
        prior_error: str = "",
    ) -> OpenAIResult:
        content: Any
        urls = [str(u or "").strip() for u in (image_urls or []) if str(u or "").strip()]
        if urls:
            content = [{"type": "text", "text": user}]
            for url in urls:
                content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            content = user
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        if not str(model).lower().startswith("gpt-5"):
            payload["temperature"] = 0.2 if urls else 0.7
        try:
            body = self._post(CHAT_COMPLETIONS_URL, payload)
            return OpenAIResult(
                text=_extract_chat_text(body),
                model=model,
                usage=body.get("usage") or {},
                raw=body,
                endpoint="chat_completions",
                error=prior_error,
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            error = f"OpenAI Chat Completions API error {exc.code}: {detail}"
        except Exception as exc:
            error = f"OpenAI Chat Completions API error: {type(exc).__name__}: {exc}"
        if prior_error:
            error = f"{prior_error}; fallback failed: {error}"
        return OpenAIResult(text="", model=model, usage={}, raw={}, endpoint="chat_completions", error=error)

    def responses_json(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: Dict[str, Any],
        reasoning_effort: str = "medium",
        store: bool = False,
    ) -> OpenAIResult:
        text_format = {
            "type": "json_schema",
            "name": schema_name,
            "strict": True,
            "schema": schema,
        }
        return self.responses_text(
            model=model,
            instructions=instructions,
            input_text=input_text,
            reasoning_effort=reasoning_effort,
            text_format=text_format,
            store=store,
            fallback_to_chat=True,
        )

    def responses_tool_step(
        self,
        *,
        model: str,
        instructions: str,
        input_payload: Any,
        tools: List[Dict[str, Any]],
        previous_response_id: str = "",
        reasoning_effort: str = "medium",
        store: bool = False,
        max_output_tokens: Optional[int] = None,
    ) -> OpenAIToolStep:
        payload: Dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_payload,
            "tools": tools,
            "tool_choice": "auto",
            "store": bool(store),
        }
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        if str(model).lower().startswith("gpt-5"):
            payload["reasoning"] = {"effort": reasoning_effort}
        if max_output_tokens:
            payload["max_output_tokens"] = int(max_output_tokens)
        try:
            body = self._post(RESPONSES_URL, payload)
            return OpenAIToolStep(
                text=_extract_response_text(body),
                model=model,
                usage=body.get("usage") or {},
                raw=body,
                endpoint="responses",
                response_id=str(body.get("id") or ""),
                tool_calls=_extract_tool_calls(body),
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            error = f"OpenAI Responses API tool error {exc.code}: {detail}"
        except Exception as exc:
            error = f"OpenAI Responses API tool error: {type(exc).__name__}: {exc}"
        return OpenAIToolStep(
            text="",
            model=model,
            usage={},
            raw={},
            endpoint="responses",
            response_id=previous_response_id,
            tool_calls=[],
            error=error,
        )
