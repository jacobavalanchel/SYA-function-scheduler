"""SYA-UI function-bridge routes consumed by Electron IPC.

These endpoints keep the FocusFlow planner on the same contract as
`app/src/features/scheduler/api.ts`. Response shapes match the renderer types
in `app/src/types/electron.d.ts` so Dashboard integration tests can call
`decompose` and `timeline.get` without a second adapter layer.
"""

from __future__ import annotations

import asyncio
from datetime import date
import json
import re
from uuid import uuid4
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agents.voyage.engine import AppContainer
from app.api.deps import get_container
from app.api.schemas import InitTaskRequest, UpdateTaskStatusRequest
from app.models.task_tree import TaskNode, TaskStatus
from app.models.persona import resolve_persona
from app.services.planning import publish_init_task, publish_status_update
from app.services.timeline import (
    minutes,
    node_to_task,
    parse_task_status,
    resolve_task_id,
    scheduler_config,
    timeline_for_tree,
)

FUNCTION_MANIFEST: dict[str, Any] = {
    "id": "scheduler",
    "version": "0.1.0",
    "runtime": "local-http",
    "entry": {"command": "bin/scheduler-server", "args": []},
    "api": {
        "basePath": "/api/scheduler",
        "health": {"method": "GET", "path": "/health"},
        "events": {"transport": "sse", "path": "/api/scheduler/events"},
    },
    "capabilities": [
        "task-decomposition",
        "timeline-scheduling",
        "task-storage",
        "hierarchical-task-tree",
        "rolling-wave-planning",
        "adaptive-replanning",
        "voyage-runtime",
        "plan-preview-confirmation",
        "persistent-plans-and-preferences",
    ],
    "events": ["task.created", "task.updated", "task.deleted", "timeline.changed"],
    "permissions": ["llm-api", "storage"],
}

router = APIRouter()


class FunctionResponse(BaseModel):
    """Standard SYA function response envelope."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: Any | None = None
    error: dict[str, Any] | None = None


class DecomposeRequest(BaseModel):
    """Natural-language decomposition request from the SYA Dashboard."""

    model_config = ConfigDict(extra="forbid", strict=True)

    input: str = Field(min_length=1)
    attachments: list[str] = Field(default_factory=list)


class ReorderTasksRequest(BaseModel):
    """Task reorder payload used by the SYA function client."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_ids: list[str] = Field(alias="taskIds", min_length=1)


def function_ok(data: Any) -> FunctionResponse:
    """Wrap successful function data in the SYA envelope."""

    return FunctionResponse(ok=True, data=data)


@router.get("/manifest")
async def manifest() -> dict[str, Any]:
    return FUNCTION_MANIFEST


def validate_plan(tree: TaskNode) -> None:
    from math import isfinite
    seen: set[str] = set()
    def walk(node: TaskNode, depth: int) -> None:
        if node.task_id in seen or len(seen) >= 300:
            raise ValueError("Plan requires unique ids and at most 300 nodes.")
        seen.add(node.task_id)
        if not node.title.strip() or depth > 3 or (not node.children and depth != 3):
            raise ValueError("Use four levels: goal, stage, task, checklist.")
        if node.status != TaskStatus.PENDING:
            raise ValueError("New plans must contain pending tasks.")
        if not node.children and (not isfinite(node.estimated_time) or not 1 <= node.estimated_time * 60 <= 1440):
            raise ValueError("Checklist duration must be finite and 1–1440 minutes.")
        for child in node.children:
            walk(child, depth + 1)
    walk(tree, 0)


class ConfirmPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tree: TaskNode


@router.post("/api/scheduler/plans/preview", response_model=FunctionResponse)
async def preview_plan(body: DecomposeRequest, container: AppContainer = Depends(get_container)) -> FunctionResponse:
    try:
        raw = await container.llm_gateway.generate_task_tree(
            goal=body.input, persona=resolve_persona("balanced"), strategy=None,
            context={"require_live_model": True, "hierarchy_depth": 4},
            response_schema=container.navigator._initial_tree_schema(),
        )
        tree = TaskNode.from_llm_json(raw)
        # Model ids are not durable identities; create fresh ids before review.
        def identify(node: TaskNode, parent: str | None = None) -> None:
            node.task_id = uuid4().hex
            node.parent_id = parent
            for child in node.children:
                identify(child, node.task_id)
        identify(tree)
        validate_plan(tree)
        return function_ok({"tree": tree.model_dump(mode="json")})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/scheduler/plans/confirm", response_model=FunctionResponse)
async def confirm_plan(body: ConfirmPlanRequest, container: AppContainer = Depends(get_container)) -> FunctionResponse:
    try:
        validate_plan(body.tree)
        await container.memory_store.confirm_plan(body.tree)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return function_ok({"tree": body.tree.model_dump(mode="json")})


@router.post("/api/scheduler/decompose", response_model=FunctionResponse)
async def scheduler_decompose(
    body: DecomposeRequest,
    container: AppContainer = Depends(get_container),
) -> FunctionResponse:
    ack = await publish_init_task(
        InitTaskRequest(goal=body.input, persona="balanced"),
        container,
        source="scheduler_api",
    )
    await container.event_bus.join()
    tree = await container.memory_store.get_task_tree_snapshot()
    tasks = [node_to_task(node) for node in (tree.iter_leaves() if tree else [])]
    return function_ok(
        {
            "tasks": tasks,
            "timeline": timeline_for_tree(tree),
            "eventId": ack.event_id,
        }
    )


@router.get("/api/scheduler/tasks", response_model=FunctionResponse)
async def scheduler_list_tasks(
    status: str | None = None,
    container: AppContainer = Depends(get_container),
) -> FunctionResponse:
    projects = await container.memory_store.list_projects()
    tasks = [node_to_task(node) for project in projects for node in project.tree.iter_nodes()]
    if status:
        wanted = {
            "in_progress": "in_progress",
            "active": "in_progress",
            "completed": "completed",
            "pending": "pending",
        }.get(status, status)
        tasks = [task for task in tasks if task["status"] == wanted]
    return function_ok(tasks)


@router.get("/api/scheduler/tasks/{taskId}", response_model=FunctionResponse)
async def scheduler_get_task(
    taskId: str,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> FunctionResponse:
    task_id = resolve_task_id(taskId, dict(request.query_params))
    raw = await container.memory_store.get_subtree_snapshot(task_id)
    node = TaskNode.model_validate_json(json.dumps(raw)) if raw else None
    if node is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return function_ok(node_to_task(node))


@router.put("/api/scheduler/tasks/{taskId}", response_model=FunctionResponse)
async def scheduler_update_task(
    taskId: str,
    body: dict[str, Any],
    container: AppContainer = Depends(get_container),
) -> FunctionResponse:
    task_id = resolve_task_id(taskId, body)
    status = parse_task_status(body.get("status"))
    if "status" in body and status is None:
        raise HTTPException(status_code=422, detail="Invalid task status")
    for key in ("deadline", "scheduledDate"):
        if body.get(key) is not None:
            try:
                body[key] = date.fromisoformat(body[key]).isoformat()
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=f"Invalid {key}") from exc
    if "estimatedMinutes" in body and (not isinstance(body["estimatedMinutes"], (int, float)) or not 1 <= body["estimatedMinutes"] <= 1440):
        raise HTTPException(status_code=422, detail="Duration must be 1–1440 minutes")
    if status is not None:
        actual_minutes = body.get("actualMinutes")
        await publish_status_update(
            task_id=task_id,
            body=UpdateTaskStatusRequest(
                status=status,
                actual_time=(float(actual_minutes) / 60 if actual_minutes else None),
            ),
            container=container,
            source="scheduler_api",
        )
        await container.event_bus.join()
        current = await container.memory_store.get_subtree_snapshot(task_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if current["status"] != status.value:
            raise HTTPException(status_code=503, detail="Task status was not saved; retry after checking storage")

    def mutator(root: TaskNode) -> dict[str, Any]:
        node = root.find_node(task_id)
        if node is None:
            raise KeyError(task_id)
        if body.get("title"):
            node.title = str(body["title"])
        if "deadline" in body:
            node.deadline = body["deadline"]
        if "scheduledDate" in body:
            node.scheduled_date = body["scheduledDate"]
        if "estimatedMinutes" in body:
            node.estimated_time = float(body["estimatedMinutes"]) / 60
        if "description" in body:
            node.description = str(body.get("description") or "")
        return node_to_task(node)

    try:
        task = await container.memory_store.mutate_tree(mutator)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    await container.event_bus.join()
    tree = await container.memory_store.get_task_tree_snapshot()
    node = tree.find_node(task_id) if tree is not None else None
    return function_ok(node_to_task(node) if node is not None else task)


@router.delete("/api/scheduler/tasks/{taskId}", response_model=FunctionResponse)
async def scheduler_delete_task(
    taskId: str,
    body: dict[str, Any] | None = None,
    container: AppContainer = Depends(get_container),
) -> FunctionResponse:
    task_id = resolve_task_id(taskId, body or {})

    def remove_from(parent: TaskNode) -> bool:
        for index, child in enumerate(parent.children):
            if child.task_id == task_id:
                del parent.children[index]
                object.__setattr__(parent, "is_leaf", len(parent.children) == 0)
                return True
            if remove_from(child):
                return True
        return False

    def mutator(root: TaskNode) -> dict[str, str]:
        if root.task_id == task_id:
            raise ValueError("Cannot delete root task")
        if not remove_from(root):
            raise KeyError(task_id)
        return {"taskId": task_id}

    try:
        return function_ok(await container.memory_store.mutate_tree(mutator))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/scheduler/timeline", response_model=FunctionResponse)
async def scheduler_timeline(container: AppContainer = Depends(get_container)) -> FunctionResponse:
    tree = await container.memory_store.get_task_tree_snapshot()
    return function_ok(timeline_for_tree(tree))


@router.post("/api/scheduler/tasks/reorder", response_model=FunctionResponse)
async def scheduler_reorder_tasks(
    body: ReorderTasksRequest,
    container: AppContainer = Depends(get_container),
) -> FunctionResponse:
    order = {task_id: index for index, task_id in enumerate(body.task_ids)}

    def mutator(root: TaskNode) -> None:
        if not any(root.find_node(task_id) for task_id in body.task_ids):
            raise KeyError(body.task_ids[0])
        for node in root.iter_nodes():
            node.children.sort(key=lambda child: order.get(child.task_id, len(order)))

    await container.memory_store.mutate_tree(mutator)
    return function_ok(None)


@router.get("/api/scheduler/stats", response_model=FunctionResponse)
async def scheduler_stats(container: AppContainer = Depends(get_container)) -> FunctionResponse:
    tree = await container.memory_store.get_task_tree_snapshot()
    leaves = list(tree.iter_leaves()) if tree is not None else []
    completed = [leaf for leaf in leaves if leaf.status == TaskStatus.DONE]
    return function_ok(
        {
            "date": date.today().isoformat(),
            "completedCount": len(completed),
            "totalEstimateMinutes": sum(minutes(leaf.estimated_time) for leaf in leaves),
            "actualMinutes": sum(minutes(leaf.actual_time) for leaf in leaves if leaf.actual_time),
        }
    )


@router.get("/api/scheduler/config", response_model=FunctionResponse)
async def scheduler_get_config(container: AppContainer = Depends(get_container)) -> FunctionResponse:
    config = scheduler_config(container.settings)
    config.update(await container.memory_store.get_config())
    return function_ok(config)


@router.put("/api/scheduler/config", response_model=FunctionResponse)
async def scheduler_update_config(
    body: dict[str, Any],
    container: AppContainer = Depends(get_container),
) -> FunctionResponse:
    config = scheduler_config(container.settings)
    config.update(await container.memory_store.get_config())
    for key in ("workStart", "workEnd"):
        if key in body and (not isinstance(body[key], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", body[key])):
            raise HTTPException(status_code=422, detail="Invalid work hours")
    if body.get("dailyGranularity", "standard") not in ("fine", "standard", "coarse"):
        raise HTTPException(status_code=422, detail="Invalid daily granularity")
    for key, value in body.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    if config.get("workStart", "09:00") >= config.get("workEnd", "18:00"):
        raise HTTPException(status_code=422, detail="Work end must be after start")
    await container.memory_store.save_config(config)
    return function_ok(config)


@router.get("/api/scheduler/events")
async def scheduler_events() -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        yield 'event: ready\ndata: {"event":"ready","payload":{"functionId":"scheduler"}}\n\n'
        while True:
            await asyncio.sleep(15)
            yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/api/scheduler/actions", response_model=FunctionResponse)
async def function_actions() -> FunctionResponse:
    return function_ok(
        {
            "functionId": FUNCTION_MANIFEST["id"],
            "actions": [
                {"action": "health", "method": "GET", "path": "/health"},
                {"action": "manifest", "method": "GET", "path": "/manifest"},
                {"action": "plans.preview", "method": "POST", "path": "/api/scheduler/plans/preview"},
                {"action": "plans.confirm", "method": "POST", "path": "/api/scheduler/plans/confirm"},
                {"action": "decompose", "method": "POST", "path": "/api/scheduler/decompose"},
                {"action": "tasks.list", "method": "GET", "path": "/api/scheduler/tasks"},
                {"action": "tasks.get", "method": "GET", "path": "/api/scheduler/tasks/{taskId}"},
                {"action": "tasks.update", "method": "PUT", "path": "/api/scheduler/tasks/{taskId}"},
                {"action": "tasks.delete", "method": "DELETE", "path": "/api/scheduler/tasks/{taskId}"},
                {"action": "timeline.get", "method": "GET", "path": "/api/scheduler/timeline"},
                {"action": "tasks.reorder", "method": "POST", "path": "/api/scheduler/tasks/reorder"},
                {"action": "stats.get", "method": "GET", "path": "/api/scheduler/stats"},
                {"action": "config.get", "method": "GET", "path": "/api/scheduler/config"},
                {"action": "config.update", "method": "PUT", "path": "/api/scheduler/config"},
            ],
        }
    )


def attach_sya_routes(app: FastAPI) -> None:
    """Register `/api/scheduler/*` on the FocusFlow FastAPI app."""

    app.include_router(router)
