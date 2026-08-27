"""Application event types for the internal event bus."""

from dataclasses import dataclass


@dataclass
class ConfigChanged:
    """Signal that settings have been written to disk.

    Handlers reload config themselves via ConfigLoader.reload(), keeping the
    event free of service-layer dependencies. ``audio_lifecycle_lock_held`` is
    set only when the publisher is awaiting handlers while it owns the shared
    audio lifecycle mutex.
    """

    audio_lifecycle_lock_held: bool = False


type AppEvent = ConfigChanged


__all__ = ["AppEvent", "ConfigChanged"]
