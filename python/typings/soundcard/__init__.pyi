from contextlib import AbstractContextManager
from typing import Protocol

import numpy as np

class _Recorder(Protocol):
    def record(self, numframes: int) -> np.ndarray: ...

class Microphone(Protocol):
    id: int | str
    name: str
    isloopback: bool

    def recorder(
        self,
        *,
        samplerate: int,
        channels: int,
        blocksize: int | None = ...,
    ) -> AbstractContextManager[_Recorder]: ...

class Speaker(Protocol):
    id: int | str
    name: str

def all_microphones(*, include_loopback: bool = ...) -> list[Microphone]: ...
def get_microphone(device: int | str, *, include_loopback: bool = ...) -> Microphone: ...
def default_speaker() -> Speaker | None: ...
def default_microphone() -> Microphone: ...
