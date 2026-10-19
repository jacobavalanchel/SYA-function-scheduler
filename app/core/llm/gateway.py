"""OpenAI-compatible planner gateway with a deterministic offline mock."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any
from uuid import uuid4

import httpx

from app.core.config import Settings
from app.models.domain_events import ReplanStrategy
from app.models.persona import PersonaProfile

logger = logging.getLogger(__name__)


class LLMPlannerGateway(ABC):
    """Abstract LLM adapter for OpenAI-compatible task decomposition."""

    @abstractmethod
    async def generate_task_tree(
        self,
        *,
        goal: str,
        persona: PersonaProfile,
        strategy: ReplanStrategy | None,
        context: dict[str, Any],
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return JSON matching the provided schema for planning operations."""


class OpenAICompatiblePlannerGateway(LLMPlannerGateway):
    """OpenAI-format client with deterministic fallback for offline development."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.openai_base_url.rstrip("/")
        self._api_key = settings.openai_api_key
        self._model = settings.openai_model
        self._timeout_seconds = settings.llm_timeout_seconds

    async def generate_task_tree(
        self,
        *,
        goal: str,
        persona: PersonaProfile,
        strategy: ReplanStrategy | None,
        context: dict[str, Any],
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate decomposition tree via OpenAI API format or local fallback."""

        if not self._api_key and context.get("require_live_model"):
            raise ValueError("Configure a model to preview AI plans, or create a manual plan.")
        if not self._api_key:
            logger.warning("LLM API key is empty; using local mock plan")
            return self._mock_plan(goal=goal, persona=persona, strategy=strategy, context=context)

        system_prompt = (
            "You are a cognitive-aware planning model. Output a single JSON object only. "
            "No markdown, no explanations, and obey this JSON Schema exactly:\n"
            f"{json.dumps(response_schema, ensure_ascii=False)}"
        )
        user_prompt = self._compose_prompt(goal, persona, strategy, context)

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
            "max_tokens": 4096,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                response_payload = response.json()

            content = response_payload["choices"][0]["message"].get("content")
            if not content:
                raise ValueError("LLM returned empty content")
            return self._load_json_content(content)
        except httpx.HTTPStatusError as exc:
            if context.get("require_live_model"):
                raise ValueError("The model request failed; your goal has been kept.") from exc
            logger.exception(
                "LLM HTTP %s: %s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            return self._mock_plan(goal=goal, persona=persona, strategy=strategy, context=context)
        except Exception as exc:
            if context.get("require_live_model"):
                raise ValueError("The model response could not be used; edit or retry your plan.") from exc
            logger.exception("LLM request failed, fallback plan will be used")
            return self._mock_plan(goal=goal, persona=persona, strategy=strategy, context=context)

    def _compose_prompt(
        self,
        goal: str,
        persona: PersonaProfile,
        strategy: ReplanStrategy | None,
        context: dict[str, Any],
    ) -> str:
        """Build prompt text with intent and efficacy-aware planning context."""

        strategy_text = strategy.value if strategy else "INITIAL"
        return (
            f"Goal: {goal}\n"
            f"Persona: {persona.model_dump(mode='json')}\n"
            f"Strategy: {strategy_text}\n"
            f"Context: {json.dumps(context, ensure_ascii=True)}\n"
            "Treat this as a long-horizon goal. Parents are multi-day or multi-week "
            "milestones; leaves are the next executable steps. Keep estimated_time in hours "
            "and produce an explicit hierarchy at least two levels deep: "
            "课题 → 阶段 → 子任务 → 清单. "
            "Write every title and description in English, even when the goal is not English."
        )

    async def translate_nodes(self, *, target: str, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate task titles and descriptions. The stored plan itself stays unchanged."""

        if not nodes or not self._api_key:
            return nodes

        language = "Simplified Chinese" if target == "zh" else "English"
        system_prompt = (
            "You translate planning tasks. Output a single JSON object only. "
            "No markdown. Keep every task_id unchanged and do not add or remove nodes."
        )
        user_prompt = (
            f"Translate each title and description into {language}. "
            "If a field is already in that language, keep its wording. "
            'Return {"nodes": [{"task_id", "title", "description"}]}.\n'
            f"{json.dumps({'nodes': nodes}, ensure_ascii=False)}"
        )
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
            "max_tokens": 4096,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"].get("content")
            parsed = self._load_json_content(content or "")
        except Exception:
            logger.exception("Task translation failed; source wording will be kept")
            return nodes

        translated = {
            str(item.get("task_id")): item
            for item in parsed.get("nodes", [])
            if isinstance(item, dict) and item.get("task_id")
        }
        merged: list[dict[str, Any]] = []
        for node in nodes:
            hit = translated.get(str(node.get("task_id")))
            title = str(hit.get("title")).strip() if hit and hit.get("title") else ""
            if not title:
                merged.append(node)
                continue
            merged.append(
                {
                    "task_id": node["task_id"],
                    "title": title,
                    "description": str(hit.get("description") or ""),
                }
            )
        return merged

    def _load_json_content(self, content: str) -> dict[str, Any]:
        """Parse JSON body and recover from fenced blocks when necessary."""

        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json", "", 1).strip()
        payload = json.loads(cleaned)
        if not isinstance(payload, dict):
            raise ValueError("Expected object JSON payload")
        return payload

    def _mock_plan(
        self,
        *,
        goal: str,
        persona: PersonaProfile,
        strategy: ReplanStrategy | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate deterministic local plans when remote LLM is unavailable."""

        if strategy is None:
            root_id = f"root-{uuid4().hex[:8]}"
            return {
                "task_id": root_id,
                "title": goal,
                "description": f"Top-level plan for persona {persona.persona_id}",
                "estimated_time": 8.0,
                "is_leaf": False,
                "children": [
                    {
                        "task_id": f"{root_id}-a",
                        "title": "Clarify success criteria",
                        "description": "Define measurable completion outcomes.",
                        "estimated_time": 1.0,
                        "is_leaf": True,
                        "children": [],
                    },
                    {
                        "task_id": f"{root_id}-b",
                        "title": "Build execution roadmap",
                        "description": "Segment milestones and dependencies.",
                        "estimated_time": 2.0,
                        "is_leaf": False,
                        "children": [
                            {
                                "task_id": f"{root_id}-b1",
                                "title": "Schedule milestone sprint",
                                "description": "Create near-term wave with concrete outputs.",
                                "estimated_time": 1.5,
                                "is_leaf": True,
                                "children": [],
                            },
                            {
                                "task_id": f"{root_id}-b2",
                                "title": "Define risk mitigation checks",
                                "description": "Prepare contingencies for blockers.",
                                "estimated_time": 1.0,
                                "is_leaf": True,
                                "children": [],
                            },
                        ],
                    },
                    {
                        "task_id": f"{root_id}-c",
                        "title": "Execute and monitor",
                        "description": "Track outcomes and capture metrics.",
                        "estimated_time": 3.0,
                        "is_leaf": True,
                        "children": [],
                    },
                ],
            }

        target_id = str(context.get("target_node_id", f"target-{uuid4().hex[:6]}"))
        if strategy == ReplanStrategy.FINE:
            return {
                "target_node_id": target_id,
                "children": [
                    {
                        "task_id": f"{target_id}-m1",
                        "title": "Prepare micro-task checklist",
                        "description": "Break pending work into 15-30 minute chunks.",
                        "estimated_time": 0.5,
                        "is_leaf": True,
                        "children": [],
                    },
                    {
                        "task_id": f"{target_id}-m2",
                        "title": "Add explicit time boxing",
                        "description": "Assign strict short deadlines for each chunk.",
                        "estimated_time": 0.5,
                        "is_leaf": True,
                        "children": [],
                    },
                    {
                        "task_id": f"{target_id}-m3",
                        "title": "Define immediate validation points",
                        "description": "Create quick completion checks after each chunk.",
                        "estimated_time": 0.5,
                        "is_leaf": True,
                        "children": [],
                    },
                ],
            }

        return {
            "target_node_id": target_id,
            "children": [
                {
                    "task_id": f"{target_id}-c1",
                    "title": "Merge routine activities",
                    "description": "Group repetitive pending steps into one macro action.",
                    "estimated_time": 2.0,
                    "is_leaf": True,
                    "children": [],
                },
                {
                    "task_id": f"{target_id}-c2",
                    "title": "Focus on milestone output",
                    "description": "Prioritize high-impact deliverables over micro tracking.",
                    "estimated_time": 2.5,
                    "is_leaf": True,
                    "children": [],
                },
            ],
        }
