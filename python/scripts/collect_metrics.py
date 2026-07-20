#!/usr/bin/env python3
"""Collect local quality metrics (basedpyright, ruff, pytest) and compare with baseline.

Usage::

    cd python && uv run python scripts/collect_metrics.py

Or via poe::

    cd python && uv run poe metrics

Outputs ``metrics/out/latest.json``, ``metrics/out/latest.md`` and optionally
``metrics/out/history/<timestamp>.json``.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast

# ── TypedDict types (used for type-safe result containers) ────────────────────


class BasedpyrightMetrics(TypedDict):
    errors: int
    warnings: int
    notes: int


class RuffMetrics(TypedDict):
    violations: int


class PytestMetrics(TypedDict):
    passed: int
    failed: int
    skipped: int
    warnings: int | None


class MetricsResult(TypedDict):
    timestamp: str
    basedpyright: BasedpyrightMetrics
    ruff: RuffMetrics
    pytest: PytestMetrics | None


class BaselineThreshold(TypedDict, total=False):
    basedpyright: BasedpyrightMetrics | None
    ruff: RuffMetrics | None
    pytest: PytestMetrics | None


# ── JSON parsing helpers (narrow opaque json.loads return to typed containers) ──


def _ensure_dict(value: object) -> dict[str, object] | None:
    """If *value* is a dict, return it as ``dict[str, object]``; otherwise ``None``."""
    if isinstance(value, dict):
        # isinstance narrows to dict[Any, Any]; cast provides clean dict[str, object] type.
        return cast("dict[str, object]", value)
    return None


def _ensure_list(value: object) -> list[object] | None:
    """If *value* is a list, return it as ``list[object]``; otherwise ``None``."""
    if isinstance(value, list):
        # isinstance narrows to list[Any]; cast provides clean list[object] type.
        return cast("list[object]", value)
    return None


def _parse_json_dict(text: str) -> dict[str, object] | None:
    """Parse JSON string; return ``dict[str, object]`` if root is a JSON object, else ``None``."""
    try:
        raw = cast("object", json.loads(text))
    except json.JSONDecodeError:
        return None
    return _ensure_dict(raw)


def _parse_json_list(text: str) -> list[object] | None:
    """Parse JSON string; return ``list[object]`` if root is a JSON array, else ``None``."""
    try:
        raw = cast("object", json.loads(text))
    except json.JSONDecodeError:
        return None
    return _ensure_list(raw)


# ── Subprocess runners ───────────────────────────────────────────────────────


def _run_tool(args: list[str], *, cwd: Path, label: str) -> subprocess.CompletedProcess[str]:
    """Run a CLI tool and return the completed process.

    Raises ``SystemExit`` if the tool is not found.
    """
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=120,
        )
    except FileNotFoundError:
        print(f"ERROR: {label} not found — is it installed in the current environment?", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"ERROR: {label} timed out after 120 s", file=sys.stderr)
        sys.exit(1)


def run_basedpyright(cwd: Path) -> str:
    """Run ``basedpyright --outputjson`` and return stdout."""
    proc = _run_tool(["basedpyright", "--outputjson"], cwd=cwd, label="basedpyright")
    return proc.stdout


def run_ruff(cwd: Path) -> str:
    """Run ``ruff check . --output-format=json`` and return stdout."""
    proc = _run_tool(["ruff", "check", ".", "--output-format=json"], cwd=cwd, label="ruff")
    return proc.stdout


def run_pytest(cwd: Path) -> str:
    """Run ``pytest --tb=short -q`` and return combined stderr+stdout.

    Returns the combined output so the summary line (written to stderr) is captured.
    """
    try:
        proc = subprocess.run(
            ["pytest", "--tb=short", "-q"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=300,
        )
    except FileNotFoundError:
        print("ERROR: pytest not found — is it installed in the current environment?", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("ERROR: pytest timed out after 300 s", file=sys.stderr)
        sys.exit(1)
    else:
        # Combine stdout and stderr; pytest writes the summary line to stderr.
        return proc.stdout + proc.stderr


# ── Parsers ───────────────────────────────────────────────────────────────────


def _safe_int(value: object, default: int = 0) -> int:
    """Convert an unknown value to int, returning *default* on failure."""
    if isinstance(value, (int, float)):
        return int(value)
    return default


def _safe_int_or_none(value: object) -> int | None:
    """Convert an unknown value to int, or None if the value is None/absent or non-numeric.

    Unlike ``_safe_int``, non-numeric values return ``None`` so that baseline
    ``null`` / invalid values are not silently treated as zero.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None  # Non-numeric → None (don't silently default to 0)


def parse_basedpyright_output(stdout: str) -> BasedpyrightMetrics:
    """Parse ``basedpyright --outputjson`` JSON output into a metrics dict.

    Returns zeroed metrics if parsing fails (prints a warning).
    """
    raw = _parse_json_dict(stdout)
    if raw is None:
        print("WARNING: could not parse basedpyright JSON output, using zeros", file=sys.stderr)
        return BasedpyrightMetrics(errors=0, warnings=0, notes=0)

    summary = _ensure_dict(raw.get("summary"))
    if summary is None:
        print("WARNING: basedpyright output missing 'summary' field, using zeros", file=sys.stderr)
        return BasedpyrightMetrics(errors=0, warnings=0, notes=0)

    return BasedpyrightMetrics(
        errors=_safe_int(summary.get("errorCount")),
        warnings=_safe_int(summary.get("warningCount")),
        notes=_safe_int(summary.get("informationCount")),
    )


def parse_ruff_output(stdout: str) -> RuffMetrics:
    """Parse ``ruff check . --output-format=json`` output into a metrics dict.

    The output is a JSON array of violations; the count is the array length.
    """
    data = _parse_json_list(stdout)
    if data is None:
        print("WARNING: could not parse ruff JSON output, using zeros", file=sys.stderr)
        return RuffMetrics(violations=0)

    return RuffMetrics(violations=len(data))


def parse_pytest_output(output: str) -> PytestMetrics:
    """Parse ``pytest --tb=short -q`` output into a metrics dict.

    Handles the summary line format::

        230 passed, 1 warning in 13.62s
        1 failed, 229 passed, 1 warning in 13.62s
        230 passed, 2 skipped, 1 warning in 13.62s
    """
    metrics = PytestMetrics(passed=0, failed=0, skipped=0, warnings=0)

    # Find the last line that matches the pytest summary pattern
    lines = output.strip().splitlines()
    summary_line = ""
    for line in reversed(lines):
        if "passed" in line or "failed" in line:
            summary_line = line.strip()
            break

    if not summary_line:
        print("WARNING: could not find pytest summary line, using zeros", file=sys.stderr)
        return metrics

    # Extract counts using regex
    passed_match = re.search(r"(\d+)\s+passed", summary_line)
    failed_match = re.search(r"(\d+)\s+failed", summary_line)
    skipped_match = re.search(r"(\d+)\s+skipped", summary_line)
    warnings_match = re.search(r"(\d+)\s+warning", summary_line)

    if passed_match:
        metrics["passed"] = int(passed_match.group(1))
    if failed_match:
        metrics["failed"] = int(failed_match.group(1))
    if skipped_match:
        metrics["skipped"] = int(skipped_match.group(1))
    if warnings_match:
        metrics["warnings"] = int(warnings_match.group(1))

    return metrics


# ── Baseline comparison ──────────────────────────────────────────────────────


class ComparisonItem:
    """Result of comparing a single metric field against its baseline."""

    key: str
    actual: int
    baseline: int | None

    def __init__(self, key: str, actual: int, baseline: int | None) -> None:
        self.key = key
        self.actual = actual
        self.baseline = baseline

    @property
    def is_checked(self) -> bool:
        """True if a baseline threshold exists for this metric."""
        return self.baseline is not None

    @property
    def is_exceeded(self) -> bool:
        """True if the actual value exceeds the baseline threshold."""
        if self.baseline is None:
            return False
        return self.actual > self.baseline

    @property
    def delta(self) -> int | None:
        """Difference from baseline (actual - baseline), or None if unchecked."""
        if self.baseline is None:
            return None
        return self.actual - self.baseline


class ComparisonGroup:
    """Group of comparison items for one tool (basedpyright, ruff, pytest)."""

    name: str
    items: list[ComparisonItem]

    def __init__(self, name: str, items: list[ComparisonItem]) -> None:
        self.name = name
        self.items = items

    @property
    def any_exceeded(self) -> bool:
        return any(item.is_exceeded for item in self.items)


def compare_metrics(
    result: MetricsResult,
    baseline: BaselineThreshold,
) -> list[ComparisonGroup]:
    """Compare metrics result against baseline and return comparison groups.

    Only fields present in the baseline are checked; fields set to ``None``
    in the baseline are skipped.
    """
    groups: list[ComparisonGroup] = []

    # basedpyright — explicit field access (no string-variable TypedDict indexing)
    bp_items: list[ComparisonItem] = []
    bp_result = result["basedpyright"]
    bp_baseline = baseline.get("basedpyright")
    bp_items.append(
        ComparisonItem(
            key="errors",
            actual=bp_result["errors"],
            baseline=bp_baseline["errors"] if bp_baseline is not None else None,
        )
    )
    bp_items.append(
        ComparisonItem(
            key="warnings",
            actual=bp_result["warnings"],
            baseline=bp_baseline["warnings"] if bp_baseline is not None else None,
        )
    )
    bp_items.append(
        ComparisonItem(
            key="notes",
            actual=bp_result["notes"],
            baseline=bp_baseline["notes"] if bp_baseline is not None else None,
        )
    )
    groups.append(ComparisonGroup("basedpyright", bp_items))

    # ruff
    ruff_items: list[ComparisonItem] = []
    ruff_result = result["ruff"]
    ruff_baseline = baseline.get("ruff")
    ruff_items.append(
        ComparisonItem(
            key="violations",
            actual=ruff_result["violations"],
            baseline=ruff_baseline["violations"] if ruff_baseline is not None else None,
        )
    )
    groups.append(ComparisonGroup("ruff", ruff_items))

    # pytest — use is-not-None checks instead of isinstance(..., dict) to preserve TypedDict info
    pytest_items: list[ComparisonItem] = []
    pytest_result = result.get("pytest")
    pytest_baseline = baseline.get("pytest")
    if pytest_result is not None:
        if pytest_baseline is not None:
            # Both result and baseline have pytest — include fields as checked
            pytest_items.append(
                ComparisonItem(
                    key="failed",
                    actual=pytest_result["failed"],
                    baseline=pytest_baseline["failed"],
                )
            )
            pytest_items.append(
                ComparisonItem(
                    key="warnings",
                    actual=_safe_int(pytest_result["warnings"]),
                    baseline=pytest_baseline["warnings"],
                )
            )
        else:
            # Baseline section missing — include fields as unchecked
            pytest_items.append(
                ComparisonItem(
                    key="failed",
                    actual=pytest_result["failed"],
                    baseline=None,
                )
            )
            pytest_items.append(
                ComparisonItem(
                    key="warnings",
                    actual=_safe_int(pytest_result["warnings"]),
                    baseline=None,
                )
            )
    groups.append(ComparisonGroup("pytest", pytest_items))

    return groups


def check_exceeded(groups: list[ComparisonGroup]) -> bool:
    """Return True if any metric exceeds its baseline."""
    return any(group.any_exceeded for group in groups)


# ── Rendering ────────────────────────────────────────────────────────────────


def render_json(result: MetricsResult) -> str:
    """Render metrics result as JSON."""
    return json.dumps(result, indent=2, ensure_ascii=False) + "\n"


def render_markdown(
    result: MetricsResult,
    groups: list[ComparisonGroup],
    *,
    previous: MetricsResult | None = None,
) -> str:
    """Render metrics result as a Markdown summary.

    Includes comparison with baseline and optional comparison with previous run.
    """
    timestamp = result["timestamp"]
    lines: list[str] = [
        f"# Quality Metrics — {timestamp}",
        "",
    ]

    for group in groups:
        lines.append(f"## {group.name}")
        lines.append("")
        for item in group.items:
            if item.is_checked:
                status = "❌ EXCEEDED" if item.is_exceeded else "✅"
                baseline_str = f"(baseline: {item.baseline})"
                delta_str = ""
                if item.delta is not None and item.delta != 0:
                    delta_str = f" [Δ{_format_delta(item.delta)}]"
                lines.append(f"- {item.key}: {item.actual} {baseline_str} {status}{delta_str}")
            else:
                lines.append(f"- {item.key}: {item.actual} (not checked)")

        # Include passed/skipped even if not in baseline
        if group.name == "pytest":
            pytest_result = result.get("pytest")
            # is-not-None preserves TypedDict type (avoids isinstance dict escape)
            if pytest_result is not None:
                lines.append(f"- passed: {pytest_result['passed']}")
                lines.append(f"- skipped: {pytest_result['skipped']}")

        lines.append("")

    # Previous-run comparison
    if previous is not None:
        lines.append("## Previous run comparison")
        lines.append("")
        for group in groups:
            # Resolve TypedDict section by explicit field name (avoids str-key access).
            if group.name == "basedpyright":
                prev_section: object = previous.get("basedpyright")
                curr_section: object = result.get("basedpyright")
            elif group.name == "ruff":
                prev_section = previous.get("ruff")
                curr_section = result.get("ruff")
            elif group.name == "pytest":
                prev_section = previous.get("pytest")
                curr_section = result.get("pytest")
            else:
                continue

            if isinstance(prev_section, dict) and isinstance(curr_section, dict):
                for item in group.items:
                    # Display-only; dict key types are runtime-checked below.
                    prev_val: object = prev_section.get(item.key)  # type: ignore[reportUnknownMemberType]
                    curr_val: object = curr_section.get(item.key)  # type: ignore[reportUnknownMemberType]
                    if isinstance(prev_val, int) and isinstance(curr_val, int):
                        diff = curr_val - prev_val
                        if diff != 0:
                            lines.append(f"- {group.name}.{item.key}: {curr_val} ({_format_delta(diff)} from previous)")
                        else:
                            lines.append(f"- {group.name}.{item.key}: {curr_val} (unchanged)")
        lines.append("")

    return "\n".join(lines)


def _format_delta(delta: int) -> str:
    """Format a delta value for display."""
    if delta > 0:
        return f"+{delta}"
    return str(delta)


# ── File I/O ─────────────────────────────────────────────────────────────────


def _parse_baseline_dict(data: dict[str, object]) -> BaselineThreshold:
    """Parse a JSON object into a BaselineThreshold, ignoring unknown fields.

    Returns an empty BaselineThreshold (all sections absent) if parsing fails.
    Missing or non-dict sections are treated as absent (unchecked).
    """
    bp_raw = _ensure_dict(data.get("basedpyright"))
    ruff_raw = _ensure_dict(data.get("ruff"))
    pytest_raw = _ensure_dict(data.get("pytest"))

    bp: BasedpyrightMetrics | None = None
    if bp_raw is not None:
        bp = BasedpyrightMetrics(
            errors=_safe_int(bp_raw.get("errors")),
            warnings=_safe_int(bp_raw.get("warnings")),
            notes=_safe_int(bp_raw.get("notes")),
        )

    ruff: RuffMetrics | None = None
    if ruff_raw is not None:
        ruff = RuffMetrics(
            violations=_safe_int(ruff_raw.get("violations")),
        )

    pytest: PytestMetrics | None = None
    if pytest_raw is not None:
        pytest = PytestMetrics(
            passed=_safe_int(pytest_raw.get("passed")),
            failed=_safe_int(pytest_raw.get("failed")),
            skipped=_safe_int(pytest_raw.get("skipped")),
            warnings=_safe_int_or_none(pytest_raw.get("warnings")),
        )

    result: BaselineThreshold = BaselineThreshold()
    if bp is not None:
        result["basedpyright"] = bp
    if ruff is not None:
        result["ruff"] = ruff
    if pytest is not None:
        result["pytest"] = pytest
    return result


def load_baseline(path: Path) -> BaselineThreshold:
    """Load baseline from a JSON file.

    Falls back to an empty baseline (all sections absent / unchecked) if the
    file does not exist, is not valid JSON, or is not a JSON object.
    """
    if not path.exists():
        print(f"WARNING: baseline file not found: {path}", file=sys.stderr)
        return BaselineThreshold()

    try:
        raw = _parse_json_dict(path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"WARNING: could not read baseline file: {exc}", file=sys.stderr)
        return BaselineThreshold()

    if raw is None:
        print("WARNING: baseline file is not a valid JSON object", file=sys.stderr)
        return BaselineThreshold()

    return _parse_baseline_dict(raw)


def _validate_metrics_result(data: dict[str, object]) -> MetricsResult | None:
    """Structural validation: check that *data* has the expected MetricResult shape.

    Returns ``MetricsResult`` if all required fields are present and correctly
    typed, otherwise ``None``.
    """
    ts = data.get("timestamp")
    if not isinstance(ts, str):
        return None

    bp_raw = _ensure_dict(data.get("basedpyright"))
    if bp_raw is None:
        return None
    bp = BasedpyrightMetrics(
        errors=_safe_int(bp_raw.get("errors")),
        warnings=_safe_int(bp_raw.get("warnings")),
        notes=_safe_int(bp_raw.get("notes")),
    )

    ruff_raw = _ensure_dict(data.get("ruff"))
    if ruff_raw is None:
        return None
    ruff = RuffMetrics(violations=_safe_int(ruff_raw.get("violations")))

    pytest_raw = data.get("pytest")
    if pytest_raw is not None:
        pytest_dict = _ensure_dict(pytest_raw)
        if pytest_dict is None:
            return None
        pytest = PytestMetrics(
            passed=_safe_int(pytest_dict.get("passed")),
            failed=_safe_int(pytest_dict.get("failed")),
            skipped=_safe_int(pytest_dict.get("skipped")),
            warnings=_safe_int_or_none(pytest_dict.get("warnings")),
        )
    else:
        pytest = None

    return MetricsResult(timestamp=ts, basedpyright=bp, ruff=ruff, pytest=pytest)


def load_previous_latest(out_dir: Path) -> MetricsResult | None:
    """Load the previous ``latest.json`` if it exists and is valid."""
    path = out_dir / "latest.json"
    if not path.exists():
        return None
    try:
        raw = _parse_json_dict(path.read_text(encoding="utf-8"))
        if raw is None:
            return None
        return _validate_metrics_result(raw)
    except OSError:
        return None


def write_latest_json(result: MetricsResult, out_dir: Path) -> None:
    """Write the latest JSON result."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "latest.json"
    _ = path.write_text(render_json(result), encoding="utf-8")
    print(f"  wrote {path}")


def write_latest_md(markdown: str, out_dir: Path) -> None:
    """Write the latest Markdown summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "latest.md"
    _ = path.write_text(markdown, encoding="utf-8")
    print(f"  wrote {path}")


def write_history_json(result: MetricsResult, out_dir: Path) -> None:
    """Write a timestamped history JSON file."""
    history_dir = out_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    ts = result["timestamp"].replace(":", "-").replace("+", "-")
    path = history_dir / f"{ts}.json"
    _ = path.write_text(render_json(result), encoding="utf-8")
    print(f"  wrote {path}")


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect local quality metrics (basedpyright, ruff, pytest) and compare with baseline.",
    )
    _ = parser.add_argument(
        "--baseline",
        default=None,
        help="Path to baseline JSON file (default: repo-root/metrics/baseline.json)",
    )
    _ = parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: repo-root/metrics/out)",
    )
    _ = parser.add_argument(
        "--skip-pytest",
        action="store_true",
        help="Skip pytest execution",
    )
    _ = parser.add_argument(
        "--no-history",
        action="store_true",
        help="Do not write timestamped history files",
    )
    return parser


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Resolve paths relative to repo root
    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[2]
    python_dir = script_path.parents[1]

    # argparse attributes are dynamically typed; cast from Any to known types
    baseline_arg: str | None = cast("str | None", args.baseline)
    out_dir_arg: str | None = cast("str | None", args.out_dir)
    skip_pytest: bool = cast("bool", args.skip_pytest)
    no_history: bool = cast("bool", args.no_history)

    baseline_path = Path(baseline_arg) if baseline_arg else repo_root / "metrics" / "baseline.json"
    out_dir = Path(out_dir_arg) if out_dir_arg else repo_root / "metrics" / "out"

    print("Collecting quality metrics …")
    print()

    # ── Collect ───────────────────────────────────────────────────────────
    timestamp = datetime.now(UTC).isoformat()

    basedpyright_stdout = run_basedpyright(cwd=python_dir)
    basedpyright_metrics = parse_basedpyright_output(basedpyright_stdout)
    bp_m = basedpyright_metrics
    print(f"  basedpyright: {bp_m['errors']} errors, {bp_m['warnings']} warnings, {bp_m['notes']} notes")

    ruff_stdout = run_ruff(cwd=python_dir)
    ruff_metrics = parse_ruff_output(ruff_stdout)
    print(f"  ruff: {ruff_metrics['violations']} violations")

    if skip_pytest:
        pytest_metrics: PytestMetrics | None = None
        print("  pytest: skipped (--skip-pytest)")
    else:
        pytest_output = run_pytest(cwd=python_dir)
        pytest_metrics = parse_pytest_output(pytest_output)
        pt = pytest_metrics
        print(
            "  pytest: {} passed, {} failed, {} skipped, {} warnings".format(
                pt["passed"], pt["failed"], pt["skipped"], pt["warnings"]
            )
        )

    result = MetricsResult(
        timestamp=timestamp,
        basedpyright=basedpyright_metrics,
        ruff=ruff_metrics,
        pytest=pytest_metrics,
    )

    # ── Load baseline ─────────────────────────────────────────────────────
    baseline = load_baseline(baseline_path)
    if not baseline:
        print("WARNING: empty baseline — no thresholds to compare against", file=sys.stderr)

    # ── Compare ───────────────────────────────────────────────────────────
    groups = compare_metrics(result, baseline)
    exceeded = check_exceeded(groups)

    # ── Previous run ──────────────────────────────────────────────────────
    previous = load_previous_latest(out_dir)

    # ── Render & write ────────────────────────────────────────────────────
    md_output = render_markdown(result, groups, previous=previous)

    write_latest_json(result, out_dir)
    write_latest_md(md_output, out_dir)

    if not no_history:
        write_history_json(result, out_dir)

    print()
    print("Markdown summary:")
    print()
    print(md_output)

    # ── Exit ──────────────────────────────────────────────────────────────
    if exceeded:
        print()
        print("❌ BASELINE EXCEEDED — some metrics are above the threshold")
        sys.exit(1)

    print()
    print("✅ All metrics within baseline")


if __name__ == "__main__":
    main()
