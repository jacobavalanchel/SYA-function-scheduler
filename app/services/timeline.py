"""Map the internal task tree to Dashboard / SYA renderer shapes."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.models.task_tree import TaskNode, TaskStatus


def minutes(hours: float | None) -> int:
    """Convert estimated hours to a UI duration in minutes."""

    return max(1, int(round(float(hours or 0.5) * 60)))


def ui_task_status(status: TaskStatus) -> str:
    if status == TaskStatus.IN_PROGRESS:
        return "in_progress"
    if status == TaskStatus.DONE:
        return "completed"
    if status == TaskStatus.SKIPPED:
        return "cancelled"
    return "pending"


def ui_event_status(status: TaskStatus, *, index: int) -> str:
    if status == TaskStatus.DONE:
        return "completed"
    if status == TaskStatus.IN_PROGRESS or index == 0:
        return "active"
    return "upcoming"


def clock(total_minutes: int) -> str:
    hours, minutes_part = divmod(max(0, total_minutes), 60)
    return f"{hours:02d}:{minutes_part:02d}"


def node_to_subtask(node: TaskNode, order: int) -> dict[str, Any]:
    return {
        "id": node.task_id,
        "title": node.title,
        "estimatedMinutes": minutes(node.estimated_time),
        "completed": node.status == TaskStatus.DONE,
        "order": order,
    }


def node_to_task(node: TaskNode) -> dict[str, Any]:
    created = datetime.now(timezone.utc).isoformat()
    return {
        "id": node.task_id,
        "title": node.title,
        "description": node.description,
        "status": ui_task_status(node.status),
        "priority": "medium",
        "estimatedMinutes": minutes(node.estimated_time),
        "deadline": node.deadline,
        "scheduledDate": node.scheduled_date,
        "parentId": node.parent_id,
        "attachments": [],
        "createdAt": created,
        "subtasks": [node_to_subtask(child, index) for index, child in enumerate(node.children)],
    }


def timeline_for_tree(root: TaskNode | None) -> dict[str, Any]:
    cursor = 9 * 60
    events: list[dict[str, Any]] = []
    leaves = list(root.iter_leaves()) if root is not None else []
    for index, leaf in enumerate(leaves):
        duration = minutes(leaf.estimated_time)
        start = cursor
        end = cursor + duration
        start_label = clock(start)
        end_label = clock(end)
        events.append(
            {
                "taskId": leaf.task_id,
                "title": leaf.title,
                "time": f"{start_label} - {end_label}",
                "startTime": start_label,
                "endTime": end_label,
                "status": ui_event_status(leaf.status, index=index),
            }
        )
        cursor = end
    return {"date": date.today().isoformat(), "events": events}


def scheduler_config(settings: Any) -> dict[str, Any]:
    return {
        "defaultDurationMinutes": 30,
        "breakDurationMinutes": 5,
        "autoScheduleEnabled": True,
        "llm": {
            "provider": "openai-compatible",
            "apiKey": "",
            "model": settings.openai_model,
            "baseUrl": settings.openai_base_url,
        },
        "storage": {"dbPath": "memory://sya-task-tree"},
        "decompose": {
            "maxSubtasks": 8,
            "defaultEstimateMinutes": 30,
            "includeBreaks": False,
            "breakDurationMinutes": 5,
        },
        "planning": {
            "completionLowThreshold": settings.completion_low_threshold,
            "completionHighThreshold": settings.completion_high_threshold,
            "evaluationLeafBatchSize": settings.evaluation_leaf_batch_size,
            "metricsWindowSize": settings.metrics_window_size,
        },
    }


def parse_task_status(value: Any) -> TaskStatus | None:
    if value is None:
        return None
    normalized = str(value).strip().upper().replace("-", "_")
    aliases = {
        "PENDING": TaskStatus.PENDING,
        "ACTIVE": TaskStatus.IN_PROGRESS,
        "IN_PROGRESS": TaskStatus.IN_PROGRESS,
        "COMPLETED": TaskStatus.DONE,
        "COMPLETE": TaskStatus.DONE,
        "DONE": TaskStatus.DONE,
        "CANCELLED": TaskStatus.SKIPPED,
        "CANCELED": TaskStatus.SKIPPED,
        "BLOCKED": TaskStatus.BLOCKED,
        "SKIPPED": TaskStatus.SKIPPED,
        "UPCOMING": TaskStatus.PENDING,
    }
    return aliases.get(normalized)


def resolve_task_id(path_value: str, payload: dict[str, Any] | None = None) -> str:
    if not path_value.startswith("{"):
        return path_value
    payload = payload or {}
    return str(payload.get("taskId") or payload.get("task_id") or path_value)
