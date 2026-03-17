"""Holds application-wide service singletons to avoid circular imports."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.monitor import MonitorService

_monitor_service: "MonitorService | None" = None


def set_monitor_service(svc: "MonitorService") -> None:
    global _monitor_service
    _monitor_service = svc


def get_monitor_service() -> "MonitorService":
    if _monitor_service is None:
        raise RuntimeError("MonitorService not initialized")
    return _monitor_service
