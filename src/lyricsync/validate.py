"""Sanity checks on the timings before anything is written to a file.

The point is to catch the cases where lyrics exist but are wrong -- a stale
LRC for a different edit of the song, an alignment that collapsed, timings
running past the end of the track -- and route them to manual review instead
of silently embedding bad data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Lyrics

# A line landing this far from the detected start of singing is suspicious.
ONSET_DRIFT_TOLERANCE = 2.5
# Allowance for a track whose last line is held over the outro.
DURATION_SLACK = 5.0


@dataclass(slots=True)
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def needs_review(self) -> bool:
        return bool(self.warnings) and not self.errors


def validate_timing(
    lyrics: Lyrics,
    *,
    duration: float | None = None,
    onset: float | None = None,
) -> ValidationReport:
    """Check a synced set of lyrics for structural and plausibility problems."""
    report = ValidationReport()
    timed = lyrics.timed_lines()

    if not timed:
        report.errors.append("no timed lines")
        return report

    starts = [line.start or 0.0 for line in timed]

    if starts[0] < 0:
        report.errors.append(f"first line starts before zero ({starts[0]:.2f}s)")

    out_of_order = sum(1 for a, b in zip(starts, starts[1:]) if b < a)
    if out_of_order:
        report.errors.append(f"{out_of_order} line(s) out of chronological order")

    if len(starts) > 1 and starts[0] == starts[-1]:
        report.errors.append("every line carries the same timestamp")

    if duration:
        overruns = [s for s in starts if s > duration + DURATION_SLACK]
        if overruns:
            report.errors.append(
                f"{len(overruns)} line(s) start after the track ends ({duration:.0f}s)"
            )
        elif starts[-1] > duration:
            report.warnings.append("last line starts past the track duration")

        # A stale LRC from a different edit usually shows up as lyrics that
        # stop long before the song does.
        if starts[-1] < duration * 0.4 and len(starts) > 4:
            report.warnings.append(
                f"lyrics end at {starts[-1]:.0f}s but the track runs {duration:.0f}s"
            )

    if onset is not None:
        drift = starts[0] - onset
        if abs(drift) > ONSET_DRIFT_TOLERANCE:
            direction = "after" if drift > 0 else "before"
            report.warnings.append(
                f"first line is {abs(drift):.1f}s {direction} the detected vocal onset"
            )

    untimed = sum(1 for line in lyrics.lines if line.start is None and not line.is_blank)
    if untimed:
        report.warnings.append(f"{untimed} line(s) have no timestamp and were dropped")

    return report
