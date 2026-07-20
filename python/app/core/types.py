"""Core type aliases shared across the application.

No project-level imports — stdlib only.
"""

from collections.abc import Callable, Coroutine
from typing import TypedDict

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

type BroadcastPayload = dict[str, JsonValue]

BroadcastFn = Callable[[BroadcastPayload], Coroutine[object, object, None]]
HandleSpeechFn = Callable[[str, str], Coroutine[object, object, None]]


class InputDevice(TypedDict):
    index: int | str
    name: str
    is_monitor: bool
    is_default: bool
    hostapi: str
    capture: str


type TomlValue = str | int | float | bool | list[TomlValue] | dict[str, TomlValue]
type TomlTable = dict[str, TomlValue]

__all__ = [
    "BroadcastFn",
    "BroadcastPayload",
    "HandleSpeechFn",
    "InputDevice",
    "JsonValue",
    "TomlTable",
    "TomlValue",
]
