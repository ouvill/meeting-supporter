"""Tests for scripts.collect_metrics — pure parsing, comparison, and rendering."""

# pyright: reportAny=false, reportUnknownVariableType=false
# Tests parse JSON output; these diagnostics are expected.

from __future__ import annotations

import json
from pathlib import Path

from scripts.collect_metrics import (
    BasedpyrightMetrics,
    BaselineThreshold,
    ComparisonItem,
    MetricsResult,
    PytestMetrics,
    RuffMetrics,
    compare_metrics,
    load_baseline,
    parse_basedpyright_output,
    parse_pytest_output,
    parse_ruff_output,
    render_json,
    render_markdown,
)


def _make_baseline(
    *,
    bp: BasedpyrightMetrics | None = None,
    ruff: RuffMetrics | None = None,
    pytest: PytestMetrics | None = None,
) -> BaselineThreshold:
    """Build a BaselineThreshold with optional sections."""
    result: BaselineThreshold = BaselineThreshold()
    if bp is not None:
        result["basedpyright"] = bp
    if ruff is not None:
        result["ruff"] = ruff
    if pytest is not None:
        result["pytest"] = pytest
    return result


def _make_result(
    *,
    bp_errors: int = 0,
    bp_warnings: int = 0,
    bp_notes: int = 0,
    ruff_violations: int = 0,
    pytest_passed: int = 0,
    pytest_failed: int = 0,
    pytest_skipped: int = 0,
    pytest_warnings: int = 0,
    include_pytest: bool = True,
) -> MetricsResult:
    """Build a MetricsResult with the given values."""
    result: MetricsResult = {
        "timestamp": "2026-06-03T07:30:00",
        "basedpyright": BasedpyrightMetrics(errors=bp_errors, warnings=bp_warnings, notes=bp_notes),
        "ruff": RuffMetrics(violations=ruff_violations),
        "pytest": (
            PytestMetrics(passed=pytest_passed, failed=pytest_failed, skipped=pytest_skipped, warnings=pytest_warnings)
            if include_pytest
            else None
        ),
    }
    return result


_DEFAULT_BASELINE = _make_baseline(
    bp=BasedpyrightMetrics(errors=0, warnings=0, notes=0),
    ruff=RuffMetrics(violations=0),
    pytest=PytestMetrics(passed=0, failed=0, skipped=0, warnings=0),
)


# ── parse_basedpyright_output ────────────────────────────────────────────────


class TestParseBasedpyrightOutput:
    def test_empty_result(self) -> None:
        stdout = json.dumps(
            {
                "version": "1.39.3",
                "time": "0",
                "generalDiagnostics": [],
                "summary": {
                    "filesAnalyzed": 0,
                    "errorCount": 0,
                    "warningCount": 0,
                    "informationCount": 0,
                    "timeInSec": 0,
                },
            }
        )
        result = parse_basedpyright_output(stdout)
        assert result == {"errors": 0, "warnings": 0, "notes": 0}

    def test_with_issues(self) -> None:
        stdout = json.dumps(
            {
                "version": "1.39.3",
                "summary": {"errorCount": 2, "warningCount": 5, "informationCount": 3},
            }
        )
        result = parse_basedpyright_output(stdout)
        assert result == {"errors": 2, "warnings": 5, "notes": 3}

    def test_missing_summary(self) -> None:
        stdout = json.dumps({"version": "1.39.3"})
        result = parse_basedpyright_output(stdout)
        assert result == {"errors": 0, "warnings": 0, "notes": 0}

    def test_invalid_json(self) -> None:
        result = parse_basedpyright_output("not json")
        assert result == {"errors": 0, "warnings": 0, "notes": 0}


# ── parse_ruff_output ────────────────────────────────────────────────────────


class TestParseRuffOutput:
    def test_empty_result(self) -> None:
        result = parse_ruff_output("[]")
        assert result == {"violations": 0}

    def test_with_violations(self) -> None:
        result = parse_ruff_output('[{"cell": "x.py", "line": 1}, {"cell": "y.py", "line": 2}]')
        assert result == {"violations": 2}

    def test_invalid_json(self) -> None:
        result = parse_ruff_output("not json")
        assert result == {"violations": 0}

    def test_not_a_list(self) -> None:
        result = parse_ruff_output('{"type": "not-an-array"}')
        assert result == {"violations": 0}


# ── parse_pytest_output ──────────────────────────────────────────────────────


class TestParsePytestOutput:
    def test_all_passed(self) -> None:
        output = "230 passed, 1 warning in 13.62s"
        result = parse_pytest_output(output)
        assert result == {"passed": 230, "failed": 0, "skipped": 0, "warnings": 1}

    def test_with_failures(self) -> None:
        output = "1 failed, 229 passed, 1 warning in 13.62s"
        result = parse_pytest_output(output)
        assert result == {"passed": 229, "failed": 1, "skipped": 0, "warnings": 1}

    def test_with_skipped(self) -> None:
        output = "230 passed, 2 skipped, 1 warning in 13.62s"
        result = parse_pytest_output(output)
        assert result == {"passed": 230, "failed": 0, "skipped": 2, "warnings": 1}

    def test_no_warnings(self) -> None:
        output = "10 passed in 0.50s"
        result = parse_pytest_output(output)
        assert result == {"passed": 10, "failed": 0, "skipped": 0, "warnings": 0}

    def test_full_realistic(self) -> None:
        output = "..............................\n..............................\n230 passed, 1 warning in 13.62s\n"
        result = parse_pytest_output(output)
        assert result == {"passed": 230, "failed": 0, "skipped": 0, "warnings": 1}

    def test_no_summary_line(self) -> None:
        output = "all good"
        result = parse_pytest_output(output)
        assert result == {"passed": 0, "failed": 0, "skipped": 0, "warnings": 0}


# ── compare_metrics ──────────────────────────────────────────────────────────


class TestCompareMetrics:
    def test_all_within_baseline(self) -> None:
        result = _make_result(bp_warnings=0, ruff_violations=0, pytest_failed=0)
        baseline = _DEFAULT_BASELINE
        groups = compare_metrics(result, baseline)
        assert not any(g.any_exceeded for g in groups)

    def test_basedpyright_warnings_exceeded(self) -> None:
        result = _make_result(bp_warnings=3)
        baseline = _DEFAULT_BASELINE
        groups = compare_metrics(result, baseline)
        bp_group = next(g for g in groups if g.name == "basedpyright")
        warning_item = next(i for i in bp_group.items if i.key == "warnings")
        assert warning_item.is_exceeded
        assert warning_item.actual == 3
        assert warning_item.baseline == 0
        assert warning_item.delta == 3

    def test_ruff_violations_exceeded(self) -> None:
        result = _make_result(ruff_violations=5)
        baseline = _DEFAULT_BASELINE
        groups = compare_metrics(result, baseline)
        ruff_group = next(g for g in groups if g.name == "ruff")
        assert ruff_group.any_exceeded

    def test_pytest_failed_exceeded(self) -> None:
        result = _make_result(pytest_failed=2)
        baseline = _DEFAULT_BASELINE
        groups = compare_metrics(result, baseline)
        pytest_group = next(g for g in groups if g.name == "pytest")
        assert pytest_group.any_exceeded

    def test_baseline_none_fields_skipped(self) -> None:
        """When pytest section is absent from baseline, pytest fields are unchecked."""
        result = _make_result(pytest_failed=0, pytest_warnings=5)
        baseline = _make_baseline(
            bp=BasedpyrightMetrics(errors=0, warnings=0, notes=0),
            ruff=RuffMetrics(violations=0),
            # pytest omitted — all pytest fields will be unchecked
        )
        groups = compare_metrics(result, baseline)
        pytest_group = next(g for g in groups if g.name == "pytest")
        assert not pytest_group.any_exceeded

    def test_empty_baseline_no_exceeded(self) -> None:
        """Empty baseline means no thresholds."""
        result = _make_result(bp_warnings=99, ruff_violations=99, pytest_failed=99)
        baseline: BaselineThreshold = BaselineThreshold()
        groups = compare_metrics(result, baseline)
        assert not any(g.any_exceeded for g in groups)

    def test_no_pytest_in_result(self) -> None:
        """When pytest is None/skipped, the group should have no exceeded items."""
        result = _make_result(include_pytest=False)
        baseline = _DEFAULT_BASELINE
        groups = compare_metrics(result, baseline)
        pytest_group = next(g for g in groups if g.name == "pytest")
        assert not pytest_group.any_exceeded


# ── ComparisonItem ───────────────────────────────────────────────────────────


class TestComparisonItem:
    def test_checked_with_baseline(self) -> None:
        item = ComparisonItem("warnings", actual=0, baseline=0)
        assert item.is_checked
        assert not item.is_exceeded
        assert item.delta == 0

    def test_exceeded(self) -> None:
        item = ComparisonItem("warnings", actual=3, baseline=0)
        assert item.is_exceeded
        assert item.delta == 3

    def test_not_checked(self) -> None:
        item = ComparisonItem("warnings", actual=5, baseline=None)
        assert not item.is_checked
        assert not item.is_exceeded
        assert item.delta is None

    def test_delta_negative(self) -> None:
        """When actual is less than baseline, delta is negative but not exceeded."""
        item = ComparisonItem("violations", actual=0, baseline=5)
        assert item.is_checked
        assert not item.is_exceeded
        assert item.delta == -5


# ── render_json ──────────────────────────────────────────────────────────────


class TestRenderJson:
    def test_basic_structure(self) -> None:
        result = _make_result(bp_errors=1, bp_warnings=2, ruff_violations=3, pytest_passed=10)
        raw = render_json(result)
        parsed: dict[str, object] = json.loads(raw)  # type: ignore[reportAny]
        assert parsed["timestamp"] == "2026-06-03T07:30:00"
        bp = parsed["basedpyright"]
        assert isinstance(bp, dict)
        assert bp["errors"] == 1
        assert bp["warnings"] == 2
        r = parsed["ruff"]
        assert isinstance(r, dict)
        assert r["violations"] == 3
        pt = parsed["pytest"]
        assert isinstance(pt, dict)
        assert pt["passed"] == 10

    def test_trailing_newline(self) -> None:
        result = _make_result()
        assert render_json(result).endswith("\n")

    def test_pytest_none(self) -> None:
        result = _make_result(include_pytest=False)
        raw = render_json(result)
        parsed: dict[str, object] = json.loads(raw)  # type: ignore[reportAny]
        assert parsed.get("pytest") is None


# ── render_markdown ──────────────────────────────────────────────────────────


class TestRenderMarkdown:
    def test_all_within_baseline(self) -> None:
        result = _make_result(pytest_passed=10)
        baseline = _DEFAULT_BASELINE
        groups = compare_metrics(result, baseline)
        md = render_markdown(result, groups)
        assert "Quality Metrics" in md
        assert "✅" in md
        assert "❌" not in md
        assert "passed: 10" in md

    def test_exceeded_highlighted(self) -> None:
        result = _make_result(bp_warnings=3)
        baseline = _DEFAULT_BASELINE
        groups = compare_metrics(result, baseline)
        md = render_markdown(result, groups)
        assert "❌ EXCEEDED" in md
        assert "Δ+3" in md or "[Δ+3]" in md

    def test_includes_timestamp(self) -> None:
        result = _make_result()
        baseline: BaselineThreshold = BaselineThreshold()
        groups = compare_metrics(result, baseline)
        md = render_markdown(result, groups)
        assert "2026-06-03T07:30:00" in md

    def test_previous_comparison(self) -> None:
        previous = _make_result(bp_warnings=2, ruff_violations=1)
        result = _make_result(bp_warnings=0, ruff_violations=3)
        baseline = _DEFAULT_BASELINE
        groups = compare_metrics(result, baseline)
        md = render_markdown(result, groups, previous=previous)
        assert "Previous run comparison" in md
        assert "Δ-2" in md or "from previous" in md

    def test_not_checked_display(self) -> None:
        """Fields with baseline=None show 'not checked'."""
        result = _make_result(pytest_warnings=3)
        baseline = _make_baseline(
            bp=BasedpyrightMetrics(errors=0, warnings=0, notes=0),
            ruff=RuffMetrics(violations=0),
            # pytest omitted entirely so all fields are "not checked"
        )
        groups = compare_metrics(result, baseline)
        md = render_markdown(result, groups)
        assert "not checked" in md


# ── Integration smoke: JSON roundtrip ────────────────────────────────────────


class TestJsonRoundtrip:
    def test_roundtrip(self) -> None:
        result = _make_result(
            bp_errors=1,
            bp_warnings=2,
            bp_notes=0,
            ruff_violations=3,
            pytest_passed=10,
            pytest_failed=0,
            pytest_skipped=1,
            pytest_warnings=2,
        )
        raw = render_json(result)
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        assert parsed["timestamp"] == result["timestamp"]
        bp = parsed["basedpyright"]
        assert isinstance(bp, dict)
        assert bp["errors"] == result["basedpyright"]["errors"]
        r = parsed["ruff"]
        assert isinstance(r, dict)
        assert r["violations"] == result["ruff"]["violations"]
        pt = parsed["pytest"]
        assert isinstance(pt, dict)
        result_pytest = result["pytest"]
        assert result_pytest is not None
        assert pt["passed"] == result_pytest["passed"]


# ── load_baseline ─────────────────────────────────────────────────────────────


class TestLoadBaseline:
    """Tests for ``load_baseline()`` with various file states."""

    def _empty_baseline(self) -> BaselineThreshold:
        return BaselineThreshold()

    def test_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.json"
        baseline = load_baseline(missing)
        assert baseline == self._empty_baseline()

    def test_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "baseline.json"
        _ = p.write_text("not json", encoding="utf-8")
        baseline = load_baseline(p)
        assert baseline == self._empty_baseline()

    def test_non_dict_root(self, tmp_path: Path) -> None:
        p = tmp_path / "baseline.json"
        _ = p.write_text("[]", encoding="utf-8")
        baseline = load_baseline(p)
        assert baseline == self._empty_baseline()

    def test_empty_object(self, tmp_path: Path) -> None:
        p = tmp_path / "baseline.json"
        _ = p.write_text("{}", encoding="utf-8")
        baseline = load_baseline(p)
        assert baseline == self._empty_baseline()

    def test_full_baseline(self, tmp_path: Path) -> None:
        p = tmp_path / "baseline.json"
        _ = p.write_text(
            json.dumps(
                {
                    "basedpyright": {"errors": 0, "warnings": 1, "notes": 2},
                    "ruff": {"violations": 3},
                    "pytest": {"passed": 10, "failed": 0, "skipped": 1, "warnings": None},
                }
            ),
            encoding="utf-8",
        )
        baseline = load_baseline(p)
        assert baseline.get("basedpyright") == BasedpyrightMetrics(errors=0, warnings=1, notes=2)
        assert baseline.get("ruff") == RuffMetrics(violations=3)
        assert baseline.get("pytest") == PytestMetrics(passed=10, failed=0, skipped=1, warnings=None)

    def test_partial_baseline_some_sections_missing(self, tmp_path: Path) -> None:
        """Only 'basedpyright' present; ruff and pytest are absent (unchecked)."""
        p = tmp_path / "baseline.json"
        _ = p.write_text(
            json.dumps({"basedpyright": {"errors": 0, "warnings": 0, "notes": 0}}),
            encoding="utf-8",
        )
        baseline = load_baseline(p)
        assert baseline.get("basedpyright") == BasedpyrightMetrics(errors=0, warnings=0, notes=0)
        assert baseline.get("ruff") is None
        assert baseline.get("pytest") is None

    def test_pytest_warnings_null_parsed_as_none(self, tmp_path: Path) -> None:
        """``pytest.warnings: null`` in baseline JSON becomes None (not checked)."""
        p = tmp_path / "baseline.json"
        _ = p.write_text(
            json.dumps(
                {
                    "basedpyright": {"errors": 0, "warnings": 0, "notes": 0},
                    "ruff": {"violations": 0},
                    "pytest": {"passed": 100, "failed": 0, "skipped": 0, "warnings": None},
                }
            ),
            encoding="utf-8",
        )
        baseline = load_baseline(p)
        pt = baseline.get("pytest")
        assert pt is not None
        # warnings=None means field is present but unchecked
        assert pt["warnings"] is None
        # Other pytest fields are normal ints
        assert pt["passed"] == 100
