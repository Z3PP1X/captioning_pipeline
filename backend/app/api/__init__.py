"""API package exposing the master router and EventBus."""
from backend.app.api.events import EventBus, get_event_bus
from backend.app.api.router import router

__all__ = ["EventBus", "get_event_bus", "router"]
