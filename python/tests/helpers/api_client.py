"""Typed wrappers for FastAPI TestClient / httpx response JSON.

``starlette.testclient.TestClient`` inherits from ``httpx.Client`` but its
method signatures reference private ``httpx._types`` aliases that basedpyright
cannot resolve.  As a result every call like ``client.get(...)`` is typed as
returning ``Unknown``, and the Unknown type propagates through ``.json()``,
``.status_code``, ``.text``, etc. across the entire test suite.

This module provides:

*   **JSON value types** — a recursive ``JsonValue`` alias validated at runtime
    so that ``Any`` from ``httpx.Response.json()`` never leaks into test code.
*   **``TypedResponse``** — a thin wrapper around ``httpx.Response`` that
    exposes ``status_code``, ``text``, ``content``, ``headers``, and a typed
    ``json()`` method.
*   **``TypedTestClient``** — a thin wrapper around ``TestClient`` whose
    ``get`` / ``post`` / ``patch`` / ``delete`` / ``options`` methods return
    ``TypedResponse``.

The ``pyright: ignore`` comments in this file are confined to the narrow
boundary where we call the untyped ``TestClient`` methods or the ``Any``-
returning ``Response.json()``.  All test code that uses these wrappers is
fully typed.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── JSON value types ────────────────────────────────────────────────────────

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]


# ── Runtime JSON validation ─────────────────────────────────────────────────


def _validate_json_value(raw: object, *, path: str = "$") -> JsonValue:
    """Recursively validate that *raw* is a legal JSON value.

    ``httpx.Response.json()`` returns ``Any``.  This function walks the
    decoded structure and returns a ``JsonValue`` that is safe to use
    without triggering ``reportAny`` / ``reportUnknown*`` diagnostics.
    """
    if raw is None or isinstance(raw, (str, bool)):
        return raw
    # ``bool`` is a subclass of ``int``, so check ``bool`` first (above).
    # ``int`` and ``float`` are both valid JSON numbers.
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return raw
    if isinstance(raw, list):
        # Narrowing from ``object`` gives ``list[Unknown]``; iterate explicitly.
        items: list[JsonValue] = []
        for i, item in enumerate(raw):  # pyright: ignore[reportUnknownArgumentType,reportUnknownVariableType]
            items.append(_validate_json_value(item, path=f"{path}[{i}]"))  # pyright: ignore[reportUnknownArgumentType]
        return items
    if isinstance(raw, dict):
        # Narrowing from ``object`` gives ``dict[Unknown, Unknown]``.
        result: dict[str, JsonValue] = {}
        for k, v in raw.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(k, str):
                raise TypeError(f"Non-string dict key at {path}: {type(k).__name__}")  # pyright: ignore[reportUnknownArgumentType]
            result[k] = _validate_json_value(v, path=f"{path}.{k}")  # pyright: ignore[reportUnknownArgumentType]
        return result
    raise TypeError(f"Unexpected JSON type at {path}: {type(raw).__name__}")


def _validate_json_object(raw: object, *, path: str = "$") -> JsonObject:
    """Validate that *raw* is a JSON object (``dict``)."""
    validated = _validate_json_value(raw, path=path)
    if not isinstance(validated, dict):
        raise TypeError(f"Expected JSON object at {path}, got {type(validated).__name__}")
    return validated


def _validate_json_array(raw: object, *, path: str = "$") -> JsonArray:
    """Validate that *raw* is a JSON array (``list``)."""
    validated = _validate_json_value(raw, path=path)
    if not isinstance(validated, list):
        raise TypeError(f"Expected JSON array at {path}, got {type(validated).__name__}")
    return validated


# ── TypedResponse ───────────────────────────────────────────────────────────


class TypedResponse:
    """Typed wrapper around ``httpx.Response``.

    Exposes the most commonly used attributes with proper types so that
    test code does not need ``cast()`` or ``# pyright: ignore`` comments.
    """

    def __init__(self, response: httpx.Response) -> None:
        self._response: httpx.Response = response

    # ── scalar attributes ───────────────────────────────────────────────

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def text(self) -> str:
        return self._response.text

    @property
    def content(self) -> bytes:
        return self._response.content

    @property
    def headers(self) -> httpx.Headers:
        return self._response.headers

    # ── JSON accessors ──────────────────────────────────────────────────

    def json(self) -> JsonValue:
        """Return the response body parsed as validated JSON."""
        # httpx.Response.json() returns Any — narrow at the boundary.
        raw: object = self._response.json()  # pyright: ignore[reportAny]
        return _validate_json_value(raw)

    def json_object(self) -> JsonObject:
        """Return the response body as a validated JSON object."""
        raw: object = self._response.json()  # pyright: ignore[reportAny]
        return _validate_json_object(raw)

    def json_array(self) -> JsonArray:
        """Return the response body as a validated JSON array."""
        raw: object = self._response.json()  # pyright: ignore[reportAny]
        return _validate_json_array(raw)


# ── TypedTestClient ─────────────────────────────────────────────────────────


class TypedTestClient:
    """Typed wrapper around ``fastapi.testclient.TestClient``.

    All HTTP verb methods return ``TypedResponse`` so that downstream
    assertions are fully typed.  Only the internal delegation calls
    carry narrow ``pyright: ignore`` comments (the ``TestClient`` method
    signatures reference private ``httpx._types`` aliases that are
    ``Unknown`` to basedpyright).
    """

    def __init__(self, app: FastAPI) -> None:
        self._client: TestClient = TestClient(app)

    # ── context manager ─────────────────────────────────────────────────

    def __enter__(self) -> TypedTestClient:
        _ = self._client.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self._client.__exit__(*args)

    # ── HTTP verbs ──────────────────────────────────────────────────────
    #
    # Each method accepts only the keyword arguments actually used by the
    # test suite, keeping the signatures explicit and ``Any``-free.

    def get(self, url: str) -> TypedResponse:
        return TypedResponse(
            self._client.get(url),  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
        )

    def post(self, url: str, *, json: object | None = None) -> TypedResponse:
        return TypedResponse(
            self._client.post(url, json=json),  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
        )

    def put(
        self,
        url: str,
        *,
        json: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> TypedResponse:
        return TypedResponse(
            self._client.put(  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                url,
                json=json,
                headers=headers,
            ),
        )

    def patch(self, url: str, *, json: object | None = None) -> TypedResponse:
        return TypedResponse(
            self._client.patch(url, json=json),  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
        )

    def delete(self, url: str, *, headers: dict[str, str] | None = None) -> TypedResponse:
        return TypedResponse(
            self._client.delete(  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                url,
                headers=headers,
            ),
        )

    def options(self, url: str, *, headers: dict[str, str] | None = None) -> TypedResponse:
        return TypedResponse(
            self._client.options(url, headers=headers),  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
        )


# ── Convenience free functions ──────────────────────────────────────────────


def response_json(response: TypedResponse) -> JsonValue:
    """Extract and validate the JSON body of a *TypedResponse*."""
    return response.json()


def response_json_object(response: TypedResponse) -> JsonObject:
    """Extract and validate the JSON body as an object."""
    return response.json_object()


def response_json_array(response: TypedResponse) -> JsonArray:
    """Extract and validate the JSON body as an array."""
    return response.json_array()


# ── Narrowing helpers ───────────────────────────────────────────────────────


def as_json_object(value: JsonValue) -> JsonObject:
    """Narrow a ``JsonValue`` to a ``JsonObject`` (dict)."""
    if isinstance(value, dict):
        return value
    raise TypeError(f"Expected JSON object, got {type(value).__name__}")


def as_json_array(value: JsonValue) -> JsonArray:
    """Narrow a ``JsonValue`` to a ``JsonArray`` (list)."""
    if isinstance(value, list):
        return value
    raise TypeError(f"Expected JSON array, got {type(value).__name__}")


def as_object_array(value: JsonValue) -> list[JsonObject]:
    """Narrow a ``JsonValue`` to a list of JSON objects.

    Useful for arrays of records (e.g. reply_agents, turns) where every
    element is expected to be a dict.
    """
    arr = as_json_array(value)
    result: list[JsonObject] = []
    for item in arr:
        if not isinstance(item, dict):
            raise TypeError(f"Expected JSON object in array, got {type(item).__name__}")
        result.append(item)
    return result
