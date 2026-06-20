"""Model-driven Responses tool orchestration for the RS agent."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from MarketingKnowledgeBase.agent.destinations import get_destination
from MarketingKnowledgeBase.agent.memory import relevant_memory_prompt
from MarketingKnowledgeBase.agent.state import append_draft, append_tool_call, append_validation, load_run, save_run
from MarketingKnowledgeBase.agent.tool_specs import agent_tool_specs
from MarketingKnowledgeBase.agent.tools import TOOL_REGISTRY, critique_content, draft_content, revise_content
from MarketingKnowledgeBase.model_router import resolve_model_for_stage
from MarketingKnowledgeBase.openai_client import OpenAIResponsesClient


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _parse_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
    return {}


def _vision_from_tool_results(tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    for call in reversed(tool_results):
        if call.get("name") != "describe_images":
            continue
        output = call.get("output") or {}
        if not isinstance(output, dict) or not output.get("ok"):
            continue
        result = output.get("result") or {}
        if isinstance(result, dict) and result.get("ok") and isinstance(result.get("result"), dict):
            return result.get("result") or {}
        if isinstance(result, dict):
            return result
    return {}


def _instructions(*, workflow_type: str, destination_id: str, mode: str) -> str:
    destination = get_destination(destination_id)
    memory = relevant_memory_prompt(
        content_type=str(destination.get("content_type") or ""),
        channel_id=destination_id,
    )
    return (
        "You are the RS content automation agent. You can use tools to gather source context, "
        "read proof images, search past wins, draft content, critique claims, apply feedback, "
        "remember rules, explain sources, queue revisions, and publish only when validation says ready.\n\n"
        "Important behavior:\n"
        "- Decide which tools are needed; do not guess facts.\n"
        "- Use describe_images before making image-based claims.\n"
        "- Use critique_draft before finalizing or publishing.\n"
        "- If critique_draft returns unsupported claims, use apply_feedback or generate_draft again to repair.\n"
        "- Never publish unless validation.ready_to_publish is true.\n"
        "- Final answer must be a JSON object with keys: draft, validation, vision, ack, tool_summary.\n"
        "- The draft object must include full_text, headline, body, cta, claims, unsupported_claims, source_refs.\n\n"
        f"Workflow: {workflow_type}\n"
        f"Mode: {mode}\n"
        f"Destination profile: {_json_dumps(destination)}\n\n"
        f"{memory}"
    )


def run_tool_calling_agent(
    *,
    run: Dict[str, Any],
    story_id: str,
    destination_id: str = "discord_what_you_missed",
    user_goal: str = "",
    mode: str = "generate",
    feedback_text: str = "",
    allow_publish: bool = False,
    max_steps: int = 8,
) -> Dict[str, Any]:
    routing = resolve_model_for_stage(
        workflow_type=str(run.get("workflow_type") or "discord_marketing_post"),
        stage="tool_orchestration",
        grounding_chars=len(user_goal) + len(feedback_text),
    )
    model = routing["model"]
    client = OpenAIResponsesClient(timeout_s=180)
    tools = agent_tool_specs(allow_publish=allow_publish)
    input_payload: Any = (
        "Run a tool-driven RS content workflow.\n"
        f"run_id: {run.get('run_id')}\n"
        f"story_id: {story_id}\n"
        f"destination_id: {destination_id}\n"
        f"mode: {mode}\n"
        f"user_goal: {user_goal}\n"
        f"feedback_text: {feedback_text}\n"
        "Use tools as needed, then return final JSON only."
    )
    previous_response_id = ""
    tool_results: List[Dict[str, Any]] = []
    final_text = ""
    last_error = ""
    for _ in range(max(1, int(max_steps))):
        step = client.responses_tool_step(
            model=model,
            instructions=_instructions(
                workflow_type=str(run.get("workflow_type") or "discord_marketing_post"),
                destination_id=destination_id,
                mode=mode,
            ),
            input_payload=input_payload,
            tools=tools,
            previous_response_id=previous_response_id,
            reasoning_effort="medium",
            store=False,
        )
        previous_response_id = step.response_id or previous_response_id
        last_error = step.error
        if step.error:
            break
        calls = step.tool_calls or []
        if not calls:
            final_text = step.text
            break
        outputs: List[Dict[str, Any]] = []
        for call in calls:
            name = str(call.get("name") or "")
            args = call.get("arguments") or {}
            call_id = str(call.get("call_id") or "")
            try:
                if name not in TOOL_REGISTRY:
                    raise ValueError(f"Unknown tool requested by model: {name}")
                result = TOOL_REGISTRY[name](**args)
                output = {"ok": True, "result": result}
            except Exception as exc:
                output = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            tool_results.append({"name": name, "arguments": args, "output": output})
            append_tool_call(load_run(str(run.get("run_id"))), name, args, output)
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _json_dumps(output),
                }
            )
        input_payload = outputs

    parsed = _parse_json_object(final_text)
    if not parsed:
        parsed = _deterministic_repairing_fallback(
            run=load_run(str(run.get("run_id"))),
            story_id=story_id,
            destination_id=destination_id,
            user_goal=user_goal,
            mode=mode,
            feedback_text=feedback_text,
        )
        parsed["orchestrator_fallback"] = True
        if last_error:
            parsed["orchestrator_error"] = last_error

    draft = parsed.get("draft") if isinstance(parsed.get("draft"), dict) else {}
    validation = parsed.get("validation") if isinstance(parsed.get("validation"), dict) else {}
    vision = parsed.get("vision") if isinstance(parsed.get("vision"), dict) else {}
    if not vision:
        vision = _vision_from_tool_results(tool_results)
    if draft:
        local_validation = critique_content(
            draft=draft,
            story_id=story_id,
            destination_id=destination_id,
            vision=vision,
        )
        validation = local_validation
        parsed["validation"] = validation
        draft["claims"] = validation.get("claims") or []
        draft["unsupported_claims"] = validation.get("unsupported_claims") or []
        draft["validation_status"] = validation.get("validation_status")
        parsed["draft"] = draft
        if vision:
            parsed["vision"] = vision
    if draft:
        append_draft(load_run(str(run.get("run_id"))), draft)
    if validation:
        append_validation(load_run(str(run.get("run_id"))), validation)
    current = load_run(str(run.get("run_id")))
    current["status"] = "ready" if validation.get("ready_to_publish") else "needs_review"
    current.setdefault("metadata", {})["orchestrator_model"] = model
    current.setdefault("metadata", {})["orchestrator_routing"] = routing
    save_run(current)
    parsed["run"] = load_run(str(run.get("run_id")))
    parsed["tool_summary"] = parsed.get("tool_summary") or tool_results
    return parsed


def _deterministic_repairing_fallback(
    *,
    run: Dict[str, Any],
    story_id: str,
    destination_id: str,
    user_goal: str,
    mode: str,
    feedback_text: str,
) -> Dict[str, Any]:
    vision: Dict[str, Any] = {}
    latest: Dict[str, Any] = {}
    for call in reversed(run.get("tool_calls") or []):
        if call.get("name") == "describe_images" and isinstance(call.get("result"), dict):
            vision = call.get("result") or {}
            break
    if mode == "revise" and run.get("final_output"):
        draft = revise_content(
            draft=run.get("final_output") or {},
            feedback_text=feedback_text or user_goal,
            story_id=story_id,
            destination_id=destination_id,
        )
    else:
        draft = draft_content(
            story_id=story_id,
            destination_id=destination_id,
            extra_instructions=user_goal,
        )
    validation = critique_content(draft=draft, story_id=story_id, destination_id=destination_id, vision=vision)
    max_repairs = 2
    repairs = 0
    while validation.get("unsupported_claims") and repairs < max_repairs:
        feedback = (
            "Repair the draft by removing or rephrasing unsupported claims. "
            f"Validation issues: {validation.get('issues')}. "
            f"Unsupported claims: {validation.get('unsupported_claims')}"
        )
        draft = revise_content(
            draft=draft,
            feedback_text=feedback,
            story_id=story_id,
            destination_id=destination_id,
        )
        validation = critique_content(draft=draft, story_id=story_id, destination_id=destination_id, vision=vision)
        repairs += 1
    draft["claims"] = validation.get("claims") or []
    draft["unsupported_claims"] = validation.get("unsupported_claims") or []
    draft["validation_status"] = validation.get("validation_status")
    return {
        "draft": draft,
        "validation": validation,
        "vision": vision,
        "ack": "I used the deterministic repair fallback and revalidated the draft.",
        "repair_attempts": repairs,
    }
