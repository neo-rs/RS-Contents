"""Responses API tool schemas for the RS content automation agent."""

from __future__ import annotations

from typing import Any, Dict, List


def _schema(properties: Dict[str, Any], required: List[str] | None = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def function_tool(name: str, description: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters,
    }


def agent_tool_specs(*, allow_publish: bool = False) -> List[Dict[str, Any]]:
    tools = [
        function_tool(
            "list_story_candidates",
            "List recent ranked RS story candidates from synced Discord knowledge.",
            _schema(
                {
                    "bucket": {"type": "string", "description": "Optional bucket filter."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                }
            ),
        ),
        function_tool(
            "get_post_assets",
            "Get reusable image/assets for a story.",
            _schema({"story_id": {"type": "string"}}, ["story_id"]),
        ),
        function_tool(
            "describe_images",
            "Read source proof images and return visible facts, uncertainty, and errors.",
            _schema(
                {
                    "story_id": {"type": "string"},
                    "max_images": {"type": "integer", "minimum": 1, "maximum": 8},
                },
                ["story_id"],
            ),
        ),
        function_tool(
            "generate_draft",
            "Generate a structured draft for a destination.",
            _schema(
                {
                    "story_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                    "extra_instructions": {"type": "string"},
                },
                ["story_id", "destination_id"],
            ),
        ),
        function_tool(
            "critique_draft",
            "Validate a draft against destination rules, source facts, image facts, and claim policy.",
            _schema(
                {
                    "draft": {"type": "object"},
                    "story_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                    "rejected": {"type": "boolean"},
                    "vision": {"type": "object"},
                },
                ["draft", "story_id", "destination_id"],
            ),
        ),
        function_tool(
            "apply_feedback",
            "Apply human feedback to the latest draft and return a revised structured draft.",
            _schema(
                {
                    "draft": {"type": "object"},
                    "feedback_text": {"type": "string"},
                    "story_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                },
                ["draft", "feedback_text", "story_id", "destination_id"],
            ),
        ),
        function_tool(
            "queue_revision",
            "Queue a draft for review/approval.",
            _schema({"draft": {"type": "object"}}, ["draft"]),
        ),
        function_tool(
            "remember_style_rule",
            "Store a durable correction/style rule as agent memory.",
            _schema(
                {
                    "rule_text": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "enum": ["global_rs_memory", "content_type_memory", "channel_memory", "do_not_claim_memory"],
                    },
                    "created_by": {"type": "string"},
                },
                ["rule_text"],
            ),
        ),
        function_tool(
            "search_past_wins",
            "Search past member wins/success stories for style and proof context.",
            _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 20}}),
        ),
        function_tool(
            "explain_claim_sources",
            "Explain which source facts, image facts, memories, and validations support the current run.",
            _schema({"run_id": {"type": "string"}}, ["run_id"]),
        ),
    ]
    if allow_publish:
        tools.append(
            function_tool(
                "publish_approved_post",
                "Publish a validation-ready draft to Discord. Requires ready_to_publish validation.",
                _schema(
                    {
                        "draft": {"type": "object"},
                        "channel_id": {"type": "integer"},
                        "validation": {"type": "object"},
                    },
                    ["draft", "validation"],
                ),
            )
        )
    return tools
