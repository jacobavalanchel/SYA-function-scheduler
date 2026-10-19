"""Runtime planning state with optional host-owned atomic snapshot persistence."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agents.voyage.checks import TaskExecutionSample
from app.models.domain_events import DomainEvent, ReplanStrategy
from app.models.task_tree import TERMINAL_STATUSES, TaskNode, TaskStatus
from app.models.voyage import Voyage, VoyagePhase

T = TypeVar("T")


class StatusTransition(BaseModel):
    """Result object describing one status transition in the task tree."""

    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: str = Field(min_length=1)
    parent_id: str | None = None
    old_status: TaskStatus
    new_status: TaskStatus
    is_leaf: bool
    estimated_time: float = Field(gt=0)
    actual_time: float | None = Field(default=None, ge=0)
    transitioned_to_terminal_leaf: bool


class PlannedProject:
    """One long-horizon 课题: its voyage context plus its own task tree."""

    def __init__(self, voyage: Voyage, tree: TaskNode) -> None:
        self.voyage = voyage
        self.tree = tree


class MemoryStore:
    """Concurrency-safe store for runtime scheduling state.

    Several 课题 can run at once. Each keeps its own tree and deadline.
    The active voyage is the one most recently created or revised, which is
    what the single-tree host routes still read.
    """

    def __init__(self, event_history_maxlen: int = 2000, data_dir: str = "") -> None:
        self._lock = asyncio.Lock()

        self._goal: str | None = None
        self._persona: str | None = None
        self._voyage: Voyage | None = None
        self._task_tree: TaskNode | None = None
        self._projects: list[PlannedProject] = []

        self._event_history: deque[DomainEvent] = deque(maxlen=event_history_maxlen)
        self._execution_log: deque[TaskExecutionSample] = deque(maxlen=event_history_maxlen)
        self._config: dict = {}
        self._state_file = Path(data_dir) / "planner.json" if data_dir else None
        if self._state_file and self._state_file.exists():
            data = json.loads(self._state_file.read_text())
            self._projects = [PlannedProject(Voyage.model_validate_json(json.dumps(row["voyage"])), TaskNode.model_validate_json(json.dumps(row["tree"]))) for row in data["projects"]]
            self._config = data.get("config", {})
            if self._projects:
                self._activate(next((p for p in self._projects if p.tree.task_id == data.get("active")), self._projects[-1]))

    @contextmanager
    def _transaction(self):
        # Copy as one object so active tree/voyage references remain coherent.
        previous = deepcopy((self._projects, self._task_tree, self._voyage, self._goal, self._persona, self._execution_log))
        try:
            yield
            self._persist()
        except Exception:
            (self._projects, self._task_tree, self._voyage, self._goal, self._persona, self._execution_log) = previous
            raise

    def _persist(self) -> None:
        if self._state_file is None:
            return
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"projects": [{"voyage": p.voyage.model_dump(mode="json"), "tree": p.tree.model_dump(mode="json")} for p in self._projects], "active": self._task_tree.task_id if self._task_tree else None, "config": self._config}
        fd, name = tempfile.mkstemp(dir=self._state_file.parent, prefix=".planner-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._state_file)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def get_config(self) -> dict:
        async with self._lock:
            return json.loads(json.dumps(self._config))

    async def save_config(self, config: dict) -> None:
        async with self._lock:
            previous = self._config
            self._config = config
            try:
                self._persist()
            except Exception:
                self._config = previous
                raise

    async def confirm_plan(self, root: TaskNode) -> None:
        async with self._lock:
            if any(p.tree.task_id == root.task_id for p in self._projects):
                raise ValueError("Plan already confirmed; reload the existing plan.")
            if not self._known_ids().isdisjoint(node.task_id for node in root.iter_nodes()):
                raise ValueError("Task ids already exist.")
            project = PlannedProject(Voyage(goal=root.title, persona="balanced"), root)
            self._projects.append(project)
            previous = self._task_tree
            self._activate(project)
            try:
                self._persist()
            except Exception:
                self._projects.pop()
                prior = next((p for p in self._projects if p.tree is previous), None)
                if prior:
                    self._activate(prior)
                else:
                    self._task_tree = None
                    self._voyage = None
                raise

    async def set_goal_context(self, *, goal: str, persona: str) -> None:
        """Persist latest top-level user objective and persona context."""
        async with self._lock:
            self._goal = goal
            self._persona = persona

    async def update_plan_context(
        self,
        *,
        task_id: str,
        goal: str,
        persona: str,
        deadline_days: int | None,
    ) -> None:
        """Revise one project's goal, grain, and deadline. Other 课题 stay put."""
        async with self._lock:
            with self._transaction():
                project = self._project_for(task_id)
                if project is None:
                    if self._voyage is None or self._task_tree is None:
                        raise ValueError("Task tree is not initialized")
                    raise KeyError(task_id)
                node = project.tree.find_node(task_id)
                if node is None:
                    raise KeyError(task_id)
                self._activate(project)
                project.voyage.goal = goal
                project.voyage.persona = persona
                project.voyage.deadline_days = deadline_days
                project.voyage.updated_at = datetime.now(timezone.utc)
                node.title = goal

    async def start_voyage(self, *, goal: str, persona: str, deadline_days: int | None = None) -> Voyage:
        """Open a new Voyage for one long-horizon 课题."""
        async with self._lock:
            self._goal = goal
            self._persona = persona
            self._voyage = Voyage(
                goal=goal,
                persona=persona,
                deadline_days=deadline_days,
                phase=VoyagePhase.PLANNING,
            )
            return self._voyage.model_copy(deep=True)

    async def set_voyage_phase(
        self,
        phase: VoyagePhase,
        *,
        human_gate_reason: str | None = None,
    ) -> None:
        """Advance the current Voyage state machine."""
        async with self._lock:
            if self._voyage is None:
                return
            self._voyage.phase = phase
            self._voyage.human_gate_reason = human_gate_reason
            self._voyage.updated_at = datetime.now(timezone.utc)

    async def get_voyage(self) -> Voyage | None:
        """Return a snapshot of the active Voyage, if any."""
        async with self._lock:
            return self._voyage.model_copy(deep=True) if self._voyage else None

    async def get_goal_context(self) -> tuple[str | None, str | None]:
        """Read current goal and persona context."""
        async with self._lock:
            return self._goal, self._persona

    async def append_event(self, event: DomainEvent) -> None:
        """Append domain event to bounded in-memory history."""
        async with self._lock:
            self._event_history.append(event)

    async def set_task_tree(self, root: TaskNode) -> None:
        """Append a 课题. An existing 课题 with the same root id is replaced."""
        async with self._lock:
            with self._transaction():
                if self._voyage is None:
                    self._voyage = Voyage(goal=root.title, persona=self._persona or "balanced")
                self._disambiguate_ids(root)
                existing = next((item for item in self._projects if item.tree.task_id == root.task_id), None)
                if existing is None:
                    project = PlannedProject(voyage=self._voyage, tree=root)
                    self._projects.append(project)
                else:
                    existing.voyage = self._voyage
                    existing.tree = root
                    project = existing
                self._activate(project)

    async def list_projects(self) -> list[PlannedProject]:
        """Return deep copies of every 课题, oldest first."""
        async with self._lock:
            return [
                PlannedProject(
                    voyage=project.voyage.model_copy(deep=True),
                    tree=project.tree.model_copy(deep=True),
                )
                for project in self._projects
            ]

    async def get_task_tree_snapshot(self) -> TaskNode | None:
        """Return a deep copy of the active task tree for single-tree routes."""
        async with self._lock:
            return self._task_tree.model_copy(deep=True) if self._task_tree else None

    async def get_subtree_snapshot(self, task_id: str) -> dict | None:
        """Return a JSON-ready subtree snapshot for targeted replanning prompts."""
        async with self._lock:
            project = self._project_for(task_id)
            if project is None:
                return None
            node = project.tree.find_node(task_id)
            if node is None:
                return None
            return node.model_dump(mode="json")

    async def mutate_tree(self, mutator: Callable[[TaskNode], T]) -> T:
        """Apply an atomic tree mutation under lock and return mutation output."""
        async with self._lock:
            with self._transaction():
                candidates = self._mutation_order()
                if not candidates:
                    raise ValueError("Task tree is not initialized")
                missed: KeyError | None = None
                for project in candidates:
                    try:
                        result = mutator(project.tree)
                    except KeyError as exc:
                        missed = exc
                        continue
                    self._activate(project)
                    return result
                if missed is not None:
                    raise missed
                raise ValueError("Task tree is not initialized")

    async def update_task_status(
        self,
        *,
        task_id: str,
        new_status: TaskStatus,
        actual_time: float | None,
    ) -> StatusTransition:
        """Update node status and append execution sample for metrics evaluation."""
        async with self._lock:
            with self._transaction():
                project = self._project_for(task_id)
                if project is None:
                    if not self._projects and self._task_tree is None:
                        raise ValueError("Task tree is not initialized")
                    raise KeyError(f"Task node not found: {task_id}")

                self._activate(project)
                node = project.tree.find_node(task_id)
                if node is None:
                    raise KeyError(f"Task node not found: {task_id}")

                old_status = node.status
                node.status = new_status
                if actual_time is not None:
                    node.actual_time = actual_time

                sample = TaskExecutionSample(
                    task_id=node.task_id,
                    status=node.status,
                    estimated_time=node.estimated_time,
                    actual_time=node.actual_time,
                )
                self._execution_log.append(sample)

                transitioned_to_terminal_leaf = (
                    node.is_leaf
                    and old_status not in TERMINAL_STATUSES
                    and node.status in TERMINAL_STATUSES
                )

                return StatusTransition(
                    task_id=node.task_id,
                    parent_id=node.parent_id,
                    old_status=old_status,
                    new_status=node.status,
                    is_leaf=node.is_leaf,
                    estimated_time=node.estimated_time,
                    actual_time=node.actual_time,
                    transitioned_to_terminal_leaf=transitioned_to_terminal_leaf,
                )

    async def get_recent_execution_samples(self, window_size: int) -> list[TaskExecutionSample]:
        """Return a recency window of execution samples for progress evaluation."""
        async with self._lock:
            if window_size <= 0:
                return []
            return list(self._execution_log)[-window_size:]

    async def select_replan_target(self, strategy: ReplanStrategy) -> str | None:
        """Select pending branch target according to requested granularity strategy."""
        async with self._lock:
            if self._task_tree is None:
                return None

            candidates: list[tuple[int, TaskNode]] = []
            self._collect_pending_branches(self._task_tree, depth=0, bucket=candidates)
            if not candidates:
                return None

            if strategy == ReplanStrategy.FINE:
                depth, node = max(candidates, key=lambda item: item[0])
            else:
                depth, node = min(candidates, key=lambda item: item[0])

            _ = depth
            return node.task_id

    def _collect_pending_branches(
        self,
        node: TaskNode,
        *,
        depth: int,
        bucket: list[tuple[int, TaskNode]],
    ) -> None:
        """Collect internal nodes that still have pending descendants."""
        has_pending_child = any(child.has_pending_descendant() for child in node.children)
        if node.children and has_pending_child:
            bucket.append((depth, node))

        for child in node.children:
            self._collect_pending_branches(child, depth=depth + 1, bucket=bucket)

    def _activate(self, project: PlannedProject) -> None:
        """Point single-tree readers at one 课题 without touching the others."""
        self._voyage = project.voyage
        self._task_tree = project.tree
        self._goal = project.voyage.goal
        self._persona = project.voyage.persona

    def _project_for(self, task_id: str) -> PlannedProject | None:
        """Find the 课题 that owns a node id."""
        for project in self._projects:
            if project.tree.find_node(task_id) is not None:
                return project
        return None

    def _mutation_order(self) -> list[PlannedProject]:
        """Try the active 课题 first, then the rest."""
        ordered: list[PlannedProject] = []
        if self._task_tree is not None:
            active = next((item for item in self._projects if item.tree is self._task_tree), None)
            if active is not None:
                ordered.append(active)
        ordered.extend(item for item in self._projects if item not in ordered)
        return ordered

    def _known_ids(self) -> set[str]:
        ids: set[str] = set()
        for project in self._projects:
            ids.update(node.task_id for node in project.tree.iter_nodes())
        return ids

    def _disambiguate_ids(self, root: TaskNode) -> None:
        """Prefix a new tree when the model reused ids from another 课题."""
        incoming = {node.task_id for node in root.iter_nodes()}
        if self._known_ids().isdisjoint(incoming):
            return
        prefix = f"p{uuid4().hex[:6]}-"

        def rewrite(node: TaskNode) -> None:
            node.task_id = f"{prefix}{node.task_id}"
            for child in node.children:
                object.__setattr__(child, "parent_id", node.task_id)
                rewrite(child)

        rewrite(root)
