"""Application event types for the internal event bus."""

from dataclasses import dataclass


@dataclass
class ConfigChanged:
    """Signal that settings have been written to disk.

    Carries no payload — handlers reload config themselves via ConfigLoader.reload().
    This keeps the event type free of service-layer dependencies.
    """


type AppEvent = ConfigChanged


__all__ = ["AppEvent", "ConfigChanged"]
