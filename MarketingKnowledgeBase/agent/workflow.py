"""High-level workflows for the RS content automation agent."""

from __future__ import annotations

from typing import Any, Dict

from MarketingKnowledgeBase.agent.destinations import get_destination
from MarketingKnowledgeBase.agent.memory import remember_rule
from MarketingKnowledgeBase.agent.orchestrator import run_tool_calling_agent
from MarketingKnowledgeBase.agent.review_chat import parse_review_message
from MarketingKnowledgeBase.agent.state import (
    append_draft,
    append_feedback,
    append_validation,
    set_active_review_run,
    latest_draft,
    list_runs,
    load_run,
    new_run,
    save_run,
)
from MarketingKnowledgeBase.agent.tools import (
    call_tool,
    critique_content,
    describe_images,
    draft_content,
    explain_sources,
    publish_content,
    revise_content,
    store_feedback,
)


def agent_generate(
    *,
    story_id: str,
    destination_id: str = "discord_what_you_missed",
    requested_by: str = "",
    target_channel: str = "",
    extra_instructions: str = "",
    use_tool_orchestrator: bool = True,
) -> Dict[str, Any]:
    run = new_run(
        workflow_type="discord_marketing_post",
        requested_by=requested_by,
        input_message=extra_instructions,
        target_channel=target_channel,
        metadata={"story_id": story_id, "destination_id": destination_id},
    )
    if use_tool_orchestrator:
        try:
            result = run_tool_calling_agent(
                run=run,
                story_id=story_id,
                destination_id=destination_id,
                user_goal=extra_instructions,
                mode="generate",
                allow_publish=False,
            )
            run = result.get("run") or load_run(run["run_id"])
            set_active_review_run(str(run.get("run_id") or ""))
            return {
                "run": run,
                "draft": result.get("draft") or {},
                "validation": result.get("validation") or {},
                "vision": result.get("vision") or {},
                "tool_summary": result.get("tool_summary") or [],
                "ack": result.get("ack") or "Draft generated with GPT-5.5 tool orchestration.",
            }
        except Exception as exc:
            run = load_run(run["run_id"])
            run.setdefault("metadata", {})["orchestrator_error"] = f"{type(exc).__name__}: {exc}"
            save_run(run)

    vision = call_tool(run, "describe_images", story_id=story_id)
    draft = call_tool(
        run,
        "draft_content",
        story_id=story_id,
        destination_id=destination_id,
        extra_instructions=extra_instructions,
    )
    validation = call_tool(
        run,
        "critique_content",
        draft=draft,
        story_id=story_id,
        destination_id=destination_id,
        rejected=False,
        vision=vision,
    )
    draft["claims"] = validation.get("claims") or []
    draft["unsupported_claims"] = validation.get("unsupported_claims") or []
    draft["validation_status"] = validation.get("validation_status")
    append_draft(run, draft)
    append_validation(run, validation)
    run = load_run(run["run_id"])
    run["status"] = "ready" if validation.get("ready_to_publish") else "needs_review"
    save_run(run)
    set_active_review_run(str(run.get("run_id") or ""))
    return {"run": run, "draft": draft, "validation": validation, "ack": "Draft generated and validated."}


def agent_revise(*, run_id: str, feedback_text: str, requested_by: str = "") -> Dict[str, Any]:
    run = load_run(run_id)
    story_id = str((run.get("metadata") or {}).get("story_id") or "")
    destination_id = str((run.get("metadata") or {}).get("destination_id") or "discord_what_you_missed")
    prev = latest_draft(run)
    append_feedback(
        run,
        {
            "event_type": "revision_request",
            "feedback_text": feedback_text,
            "created_by": requested_by,
        },
    )
    store_feedback(run_id=run_id, feedback_text=feedback_text, event_type="revision_request", created_by=requested_by)
    try:
        result = run_tool_calling_agent(
            run=load_run(run_id),
            story_id=story_id,
            destination_id=destination_id,
            user_goal=feedback_text,
            mode="revise",
            feedback_text=feedback_text,
            allow_publish=False,
        )
        return {
            "run": result.get("run") or load_run(run_id),
            "draft": result.get("draft") or {},
            "validation": result.get("validation") or {},
            "vision": result.get("vision") or {},
            "tool_summary": result.get("tool_summary") or [],
            "ack": result.get("ack")
            or "Got it. I used the tool-calling agent to apply the feedback and revalidate it.",
        }
    except Exception as exc:
        run = load_run(run_id)
        run.setdefault("metadata", {})["orchestrator_error"] = f"{type(exc).__name__}: {exc}"
        save_run(run)

    draft = revise_content(
        draft=prev,
        feedback_text=feedback_text,
        story_id=story_id,
        destination_id=destination_id,
    )
    validation = critique_content(draft=draft, story_id=story_id, destination_id=destination_id)
    draft["claims"] = validation.get("claims") or []
    draft["unsupported_claims"] = validation.get("unsupported_claims") or []
    draft["validation_status"] = validation.get("validation_status")
    run = load_run(run_id)
    append_draft(run, draft)
    append_validation(run, validation)
    run = load_run(run_id)
    run["status"] = "ready" if validation.get("ready_to_publish") else "needs_review"
    save_run(run)
    return {
        "run": run,
        "draft": draft,
        "validation": validation,
        "ack": "Got it. I applied the feedback, kept the draft grounded, and revalidated it.",
    }


def agent_remember(
    *,
    run_id: str = "",
    rule_text: str,
    requested_by: str = "",
    scope: str = "global_rs_memory",
) -> Dict[str, Any]:
    row = remember_rule(rule_text, scope=scope, created_by=requested_by)
    if run_id:
        run = load_run(run_id)
        append_feedback(
            run,
            {"event_type": "remember_rule", "feedback_text": rule_text, "created_by": requested_by},
        )
    return {"ok": True, "memory": row, "ack": "Remembered. I will apply that rule to future drafts."}


def agent_explain(*, run_id: str) -> Dict[str, Any]:
    run = load_run(run_id)
    return {"run_id": run_id, "explanation": explain_sources(run=run)}


def agent_publish(*, run_id: str, channel_id: int = 0, requested_by: str = "") -> Dict[str, Any]:
    run = load_run(run_id)
    draft = latest_draft(run)
    if not draft:
        raise ValueError(f"Run has no draft: {run_id}")
    story_id = str((run.get("metadata") or {}).get("story_id") or "")
    destination_id = str((run.get("metadata") or {}).get("destination_id") or "discord_what_you_missed")
    validation = critique_content(draft=draft, story_id=story_id, destination_id=destination_id)
    append_validation(run, validation)
    destination = get_destination(destination_id)
    if destination.get("auto_publish_policy") == "dry_run_only":
        result = {"ok": False, "blocked": True, "reason": "destination is draft-only"}
    else:
        result = publish_content(draft=draft, channel_id=channel_id, validation=validation)
    run = load_run(run_id)
    run.setdefault("publish_attempts", []).append(
        {"requested_by": requested_by, "channel_id": channel_id, "validation": validation, "result": result}
    )
    run["status"] = "published" if result.get("ok") else "publish_blocked"
    save_run(run)
    return {"run": run, "publish_result": result, "validation": validation}


def agent_handle_review_message(
    *,
    run_id: str,
    message_text: str,
    requested_by: str = "",
    channel_id: int = 0,
) -> Dict[str, Any]:
    parsed = parse_review_message(message_text)
    command = parsed["command"]
    arg = parsed["argument"]
    if command == "revise":
        return {"command": command, **agent_revise(run_id=run_id, feedback_text=arg, requested_by=requested_by)}
    if command == "remember":
        return {"command": command, **agent_remember(run_id=run_id, rule_text=arg, requested_by=requested_by)}
    if command == "explain":
        return {"command": command, **agent_explain(run_id=run_id)}
    if command == "image":
        run = load_run(run_id)
        story_id = str((run.get("metadata") or {}).get("story_id") or "")
        result = call_tool(run, "describe_images", story_id=story_id)
        return {"command": command, "vision": result, "ack": "I re-read the available source images."}
    if command == "approve":
        run = load_run(run_id)
        append_feedback(run, {"event_type": "approve", "feedback_text": "", "created_by": requested_by})
        run = load_run(run_id)
        run["status"] = "approved"
        save_run(run)
        return {"command": command, "run": run, "ack": "Approved. This run is marked approved."}
    if command == "publish":
        return {"command": command, **agent_publish(run_id=run_id, channel_id=channel_id, requested_by=requested_by)}
    if command == "reject":
        run = load_run(run_id)
        append_feedback(run, {"event_type": "reject", "feedback_text": arg, "created_by": requested_by})
        store_feedback(run_id=run_id, feedback_text=arg, event_type="reject", created_by=requested_by)
        run = load_run(run_id)
        run["status"] = "rejected"
        save_run(run)
        return {"command": command, "run": run, "ack": "Rejected and stored. I will not publish this run."}
    if command == "regenerate":
        run = load_run(run_id)
        story_id = str((run.get("metadata") or {}).get("story_id") or "")
        destination_id = str((run.get("metadata") or {}).get("destination_id") or "discord_what_you_missed")
        return {"command": command, **agent_generate(story_id=story_id, destination_id=destination_id, requested_by=requested_by)}
    if command == "status":
        run = load_run(run_id)
        return {"command": command, "run": run, "latest_draft": latest_draft(run)}
    if command == "undo":
        run = load_run(run_id)
        drafts = run.get("drafts") or []
        if len(drafts) > 1:
            drafts.pop()
            run["drafts"] = drafts
            run["final_output"] = drafts[-1]
            run["status"] = "drafted"
            save_run(run)
        return {"command": command, "run": load_run(run_id), "ack": "Reverted to the previous draft."}
    return {
        "command": "unknown",
        "ack": "I could not confidently map that to revise, remember, explain, image, approve, publish, reject, regenerate, status, or undo.",
        "parsed": parsed,
    }


def agent_list_runs(*, limit: int = 25) -> Dict[str, Any]:
    return {"runs": list_runs(limit=limit)}


def agent_show_run(*, run_id: str) -> Dict[str, Any]:
    return {"run": load_run(run_id)}
