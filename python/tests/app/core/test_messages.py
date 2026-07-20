import pytest
from pydantic import ValidationError

from app.core.messages import CancelReplyMsg, GenerateReplyMsg, ReplyCancelResultMsg, _incoming_ta


def test_generate_reply_message_validates_without_target() -> None:
    msg = _incoming_ta.validate_python({"type": "generate_reply", "generation_id": "generation-1"})

    assert isinstance(msg, GenerateReplyMsg)
    assert msg.target_utterance_id is None
    assert msg.generation_id == "generation-1"


def test_generate_reply_message_validates_with_target() -> None:
    msg = _incoming_ta.validate_python(
        {
            "type": "generate_reply",
            "generation_id": "generation-2",
            "target_utterance_id": "utt-1",
        }
    )

    assert isinstance(msg, GenerateReplyMsg)
    assert msg.target_utterance_id == "utt-1"
    assert msg.generation_id == "generation-2"


def test_generate_reply_message_rejects_missing_generation_id() -> None:
    with pytest.raises(ValidationError):
        _ = _incoming_ta.validate_python({"type": "generate_reply", "target_utterance_id": "utt-1"})


def test_cancel_reply_message_requires_generation_and_target() -> None:
    msg = _incoming_ta.validate_python(
        {
            "type": "cancel_reply",
            "generation_id": "generation-1",
            "target_utterance_id": "utterance-1",
        }
    )

    assert isinstance(msg, CancelReplyMsg)
    assert msg.generation_id == "generation-1"
    assert msg.target_utterance_id == "utterance-1"

    with pytest.raises(ValidationError):
        _ = _incoming_ta.validate_python(
            {
                "type": "cancel_reply",
                "generation_id": "generation-1",
            }
        )


def test_reply_cancel_result_validates_status_and_cancelled_ids() -> None:
    result = ReplyCancelResultMsg(
        generation_id="generation-1",
        target_utterance_id="utterance-1",
        status="applied",
        cancelled_suggestion_ids=["suggestion-1"],
    )

    assert result.model_dump() == {
        "type": "reply_cancel_result",
        "generation_id": "generation-1",
        "target_utterance_id": "utterance-1",
        "status": "applied",
        "cancelled_suggestion_ids": ["suggestion-1"],
    }

    with pytest.raises(ValidationError):
        _ = ReplyCancelResultMsg(
            generation_id="generation-1",
            target_utterance_id="utterance-1",
            status="cancelled",  # pyright: ignore[reportArgumentType]
            cancelled_suggestion_ids=[],
        )
