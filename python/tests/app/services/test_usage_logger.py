import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from app.services.usage_logger import UsageBudget, UsageLogger


def _usage_line(
    *,
    ts: datetime,
    meeting_id: str,
    agent_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost_jpy: float,
) -> str:
    return json.dumps(
        {
            "schema_version": 2,
            "ts": ts.isoformat(timespec="seconds"),
            "meeting_id": meeting_id,
            "agent_id": agent_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "elapsed_s": 0.25,
            "estimated_cost_jpy": estimated_cost_jpy,
        },
        ensure_ascii=False,
    )


def test_log_persists_meeting_id_agent_model_and_estimated_cost(tmp_path: Path) -> None:
    usage_path = tmp_path / "usage.jsonl"
    logger = UsageLogger(usage_path, get_meeting_id=lambda: "meeting-alpha")

    logger.log(
        agent_id="reply_main",
        model="openai/gpt-5.4-nano",
        input_tokens=500_000,
        output_tokens=250_000,
        elapsed_s=1.23456,
    )

    raw_line = usage_path.read_text(encoding="utf-8").splitlines()[0]
    raw = cast("dict[str, object]", json.loads(raw_line))
    assert raw["meeting_id"] == "meeting-alpha"
    assert raw["agent_id"] == "reply_main"
    assert raw["model"] == "openai/gpt-5.4-nano"
    assert raw["input_tokens"] == 500_000
    assert raw["output_tokens"] == 250_000
    assert raw["elapsed_s"] == 1.235
    assert raw["estimated_cost_jpy"] == 12.0

    [record] = logger.records()
    assert record.meeting_id == "meeting-alpha"
    assert record.agent_id == "reply_main"
    assert record.model == "openai/gpt-5.4-nano"
    assert record.estimated_cost_jpy == 12.0


def test_summarize_aggregates_token_and_cost_totals_for_meeting_and_month(tmp_path: Path) -> None:
    usage_path = tmp_path / "usage.jsonl"
    now = datetime(2026, 7, 7, tzinfo=UTC)
    previous_month = now - timedelta(days=40)
    _ = usage_path.write_text(
        "\n".join(
            [
                _usage_line(
                    ts=now,
                    meeting_id="meeting-alpha",
                    agent_id="reply_main",
                    model="openai/gpt-5.4-nano",
                    input_tokens=500_000,
                    output_tokens=250_000,
                    estimated_cost_jpy=12.0,
                ),
                _usage_line(
                    ts=now,
                    meeting_id="meeting-alpha",
                    agent_id="info",
                    model="gemini/gemini-2.5-flash-lite",
                    input_tokens=1_000_000,
                    output_tokens=1_000_000,
                    estimated_cost_jpy=80.0,
                ),
                _usage_line(
                    ts=now,
                    meeting_id="meeting-beta",
                    agent_id="reply_main",
                    model="openai/gpt-5.4-nano",
                    input_tokens=10,
                    output_tokens=20,
                    estimated_cost_jpy=0.001,
                ),
                _usage_line(
                    ts=previous_month,
                    meeting_id="meeting-alpha",
                    agent_id="reply_main",
                    model="openai/gpt-5.4-nano",
                    input_tokens=100,
                    output_tokens=200,
                    estimated_cost_jpy=7.0,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    logger = UsageLogger(usage_path)

    current_meeting = logger.summarize(meeting_id="meeting-alpha", month=now)

    assert current_meeting.request_count == 2
    assert current_meeting.input_tokens == 1_500_000
    assert current_meeting.output_tokens == 1_250_000
    assert current_meeting.estimated_cost_jpy == 92.0
    assert {(record.meeting_id, record.agent_id, record.model) for record in logger.records()} >= {
        ("meeting-alpha", "reply_main", "openai/gpt-5.4-nano"),
        ("meeting-alpha", "info", "gemini/gemini-2.5-flash-lite"),
    }


def test_budget_gate_treats_zero_as_disabled_and_blocks_at_reached_limit(tmp_path: Path) -> None:
    usage_path = tmp_path / "usage.jsonl"
    logger = UsageLogger(usage_path)
    logger.log(
        meeting_id="meeting-alpha",
        agent_id="reply_main",
        model="openai/gpt-5.4-nano",
        input_tokens=500_000,
        output_tokens=250_000,
        elapsed_s=0.5,
    )

    assert not logger.is_budget_exceeded(
        UsageBudget(meeting_limit_jpy=0.0, monthly_limit_jpy=0.0),
        meeting_id="meeting-alpha",
    )
    assert not logger.is_budget_exceeded(
        UsageBudget(meeting_limit_jpy=12.01, monthly_limit_jpy=0.0),
        meeting_id="meeting-alpha",
    )
    assert logger.is_budget_exceeded(
        UsageBudget(meeting_limit_jpy=12.0, monthly_limit_jpy=0.0),
        meeting_id="meeting-alpha",
    )
    assert logger.is_budget_exceeded(
        UsageBudget(meeting_limit_jpy=0.0, monthly_limit_jpy=12.0),
        meeting_id="meeting-alpha",
        now=datetime.now(UTC),
    )
