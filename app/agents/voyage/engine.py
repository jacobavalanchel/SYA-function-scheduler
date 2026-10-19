"""Voyage runtime shell: wire Navigator, Helm, Sextant, and the event bus."""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.voyage.helm import Helm
from app.agents.voyage.navigator import Navigator
from app.agents.voyage.plan_edit import PlanEditor
from app.agents.voyage.sextant import Sextant
from app.core.config import Settings
from app.core.event_bus import AsyncEventBus
from app.core.llm.gateway import OpenAICompatiblePlannerGateway
from app.core.memory_store import MemoryStore
from app.services.ws_sync_service import WebSocketSyncService


@dataclass(slots=True)
class AppContainer:
    """Runtime service graph used by the desktop App and SYA function host."""

    settings: Settings
    event_bus: AsyncEventBus
    memory_store: MemoryStore
    navigator: Navigator
    helm: Helm
    sextant: Sextant
    plan_editor: PlanEditor
    ws_sync_service: WebSocketSyncService
    llm_gateway: OpenAICompatiblePlannerGateway

    @property
    def cognitive_engine(self) -> Navigator:
        """Compatibility alias for the previous CognitiveEngine name."""

        return self.navigator

    @property
    def rolling_planner(self) -> PlanEditor:
        """Compatibility alias for the previous RollingWavePlanner name."""

        return self.plan_editor

    @property
    def task_manager(self) -> Helm:
        """Compatibility alias for the previous TaskManager name."""

        return self.helm

    def register(self) -> None:
        """Subscribe Voyage components and persist every domain event."""

        self.navigator.register()
        self.plan_editor.register()
        self.helm.register()
        self.sextant.register()
        self.ws_sync_service.register()
        self.event_bus.subscribe(None, self.memory_store.append_event)


def build_container(settings: Settings) -> AppContainer:
    """Construct and wire the local Voyage runtime."""

    event_bus = AsyncEventBus()
    memory_store = MemoryStore(event_history_maxlen=settings.event_history_maxlen, data_dir=settings.scheduler_data_dir)
    llm_gateway = OpenAICompatiblePlannerGateway(settings)

    navigator = Navigator(
        event_bus=event_bus,
        memory_store=memory_store,
        llm_gateway=llm_gateway,
    )
    plan_editor = PlanEditor(event_bus=event_bus, memory_store=memory_store)
    helm = Helm(event_bus=event_bus, memory_store=memory_store)
    sextant = Sextant(
        event_bus=event_bus,
        memory_store=memory_store,
        settings=settings,
    )
    ws_sync_service = WebSocketSyncService(event_bus=event_bus)

    return AppContainer(
        settings=settings,
        event_bus=event_bus,
        memory_store=memory_store,
        navigator=navigator,
        helm=helm,
        sextant=sextant,
        plan_editor=plan_editor,
        ws_sync_service=ws_sync_service,
        llm_gateway=llm_gateway,
    )
