import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import AgentDepsT, RunContext

logger = logging.getLogger(__name__)
_DEBUG = os.getenv("DEBUG", "").lower() in ("1", "true", "yes")
_SCHEMA_VERSION = 2
_JPY_PER_USD = 160.0


@dataclass(frozen=True)
class ModelPrice:
    input_jpy_per_million: float
    output_jpy_per_million: float


@dataclass
class UsageBudget:
    meeting_limit_jpy: float = 0.0
    monthly_limit_jpy: float = 0.0


@dataclass
class UsageRecord:
    schema_version: int
    ts: str
    meeting_id: str | None
    agent_id: str
    model: str
    input_tokens: int
    output_tokens: int
    elapsed_s: float
    estimated_cost_jpy: float


@dataclass
class UsageSummary:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_jpy: float = 0.0
    request_count: int = 0


_PRICE_TABLE: dict[str, ModelPrice] = {
    "gemini-3.1-flash-lite": ModelPrice(12.0, 48.0),
    "gemini-3-flash-preview": ModelPrice(16.0, 64.0),
    "gemini-2.5-flash-lite": ModelPrice(16.0, 64.0),
    "gemini-2.5-flash": ModelPrice(48.0, 192.0),
    "gpt-5.4-nano": ModelPrice(8.0, 32.0),
    "gpt-5.4-mini": ModelPrice(32.0, 128.0),
    "claude-haiku-4-5-20251001": ModelPrice(48.0, 240.0),
}


def _model_key(model: str) -> str:
    tail = model.rsplit("/", 1)[-1]
    return tail.rsplit(":", 1)[-1]


def estimate_cost_jpy(model: str, input_tokens: int, output_tokens: int) -> float:
    price = _PRICE_TABLE.get(_model_key(model))
    if price is None:
        return 0.0
    cost = (input_tokens * price.input_jpy_per_million + output_tokens * price.output_jpy_per_million) / 1_000_000
    return round(cost, 6)


def _same_month(ts: str, now: datetime) -> bool:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.year == now.year and dt.month == now.month


class UsageLogger:
    def __init__(self, path: Path, get_meeting_id: Callable[[], str | None] | None = None) -> None:
        self._path: Path = path
        self._get_meeting_id: Callable[[], str | None] | None = get_meeting_id
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        *,
        agent_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        elapsed_s: float,
        meeting_id: str | None = None,
    ) -> None:
        scoped_meeting_id = meeting_id
        if scoped_meeting_id is None and self._get_meeting_id is not None:
            scoped_meeting_id = self._get_meeting_id()
        record = UsageRecord(
            schema_version=_SCHEMA_VERSION,
            ts=datetime.now(UTC).isoformat(timespec="seconds"),
            meeting_id=scoped_meeting_id,
            agent_id=agent_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_s=round(elapsed_s, 3),
            estimated_cost_jpy=estimate_cost_jpy(model, input_tokens, output_tokens),
        )
        line = json.dumps(asdict(record), ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as f:
            _ = f.write(line + "\n")

    def records(self) -> list[UsageRecord]:
        if not self._path.exists():
            return []
        result: list[UsageRecord] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            try:
                raw_obj = cast("object", json.loads(line))
            except json.JSONDecodeError:
                continue
            if not isinstance(raw_obj, dict):
                continue
            raw = cast("dict[str, object]", raw_obj)
            input_tokens = _int_field(raw, "input_tokens")
            output_tokens = _int_field(raw, "output_tokens")
            model = _str_field(raw, "model")
            result.append(
                UsageRecord(
                    schema_version=_int_field(raw, "schema_version", default=1),
                    ts=_str_field(raw, "ts"),
                    meeting_id=_optional_str_field(raw, "meeting_id"),
                    agent_id=_str_field(raw, "agent_id"),
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    elapsed_s=_float_field(raw, "elapsed_s"),
                    estimated_cost_jpy=_float_field(
                        raw,
                        "estimated_cost_jpy",
                        default=estimate_cost_jpy(model, input_tokens, output_tokens),
                    ),
                )
            )
        return result

    def summarize(
        self,
        *,
        meeting_id: str | None = None,
        month: datetime | None = None,
    ) -> UsageSummary:
        summary = UsageSummary()
        for record in self.records():
            if meeting_id is not None and record.meeting_id != meeting_id:
                continue
            if month is not None and not _same_month(record.ts, month):
                continue
            summary.input_tokens += record.input_tokens
            summary.output_tokens += record.output_tokens
            summary.estimated_cost_jpy += record.estimated_cost_jpy
            summary.request_count += 1
        summary.estimated_cost_jpy = round(summary.estimated_cost_jpy, 6)
        return summary

    def is_budget_exceeded(
        self,
        budget: UsageBudget,
        *,
        meeting_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        if budget.meeting_limit_jpy > 0 and meeting_id is not None:
            if self.summarize(meeting_id=meeting_id).estimated_cost_jpy >= budget.meeting_limit_jpy:
                return True
        if budget.monthly_limit_jpy > 0:
            if self.summarize(month=now or datetime.now(UTC)).estimated_cost_jpy >= budget.monthly_limit_jpy:
                return True
        return False


def _str_field(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) else ""


def _optional_str_field(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) else None


def _int_field(raw: dict[str, object], key: str, *, default: int = 0) -> int:
    value = raw.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _float_field(raw: dict[str, object], key: str, *, default: float = 0.0) -> float:
    value = raw.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def make_logging_hooks(agent_id: str, usage_logger: UsageLogger | None = None) -> Hooks[AgentDepsT]:
    """Return a Hooks capability that logs timing and token usage for every model request."""
    hooks = Hooks[AgentDepsT]()

    @hooks.on.model_request
    async def _wrap(  # pyright: ignore[reportUnusedFunction]
        ctx: RunContext[AgentDepsT],
        /,
        *,
        request_context: ModelRequestContext,
        handler: Callable[[ModelRequestContext], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if _DEBUG:
            logger.debug("%s リクエスト開始", agent_id)
        t0 = time.perf_counter()
        response: ModelResponse = await handler(request_context)
        elapsed = time.perf_counter() - t0

        usage: object = getattr(response, "usage", None)
        in_t: object = getattr(usage, "input_tokens", None) if usage is not None else None
        out_t: object = getattr(usage, "output_tokens", None) if usage is not None else None

        if usage_logger and isinstance(in_t, int) and isinstance(out_t, int):
            model_name: str = getattr(ctx.model, "model_name", None) or str(ctx.model)
            usage_logger.log(
                agent_id=agent_id,
                model=model_name,
                input_tokens=in_t,
                output_tokens=out_t,
                elapsed_s=elapsed,
            )

        if _DEBUG:
            usage_str = f" in={in_t} out={out_t}" if in_t is not None else ""
            logger.debug("%s 完了 elapsed=%.2fs%s", agent_id, elapsed, usage_str)

        return response

    return hooks
