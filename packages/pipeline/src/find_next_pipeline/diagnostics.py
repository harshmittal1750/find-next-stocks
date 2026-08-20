"""Fold repeated failures into counted kinds.

A refresh stage records progress well, but each failure overwrites the last
(``"Latest batch failed: …"``). When twenty batches fail with the same rate-limit error
and one fails with a schema change, whichever landed last is the only one reported. The
count is lost, and so is the distinct failure that actually needs attention.

:class:`FailureTally` keeps both. Messages are normalised before grouping, so
``"timed out after 30s"`` and ``"timed out after 45s"`` fold into one kind seen twice
rather than two singletons — the thing you want to know is that timeouts happened
twenty-one times, not the exact durations.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator

__all__ = ["FailureTally", "normalise_detail"]

_NUMBER = re.compile(r"\d+")
_HEX = re.compile(r"0x[0-9a-fA-F]+")
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(r"(/[\w.\-]+){2,}")
_WHITESPACE = re.compile(r"\s+")

MAX_DETAIL = 160


def normalise_detail(detail: object) -> str:
    """Reduce a message to a stable signature so near-identical failures group."""
    text = str(detail or "")
    text = _URL.sub("<url>", text)
    text = _HEX.sub("<addr>", text)
    text = _PATH.sub("<path>", text)
    text = _NUMBER.sub("#", text)
    return _WHITESPACE.sub(" ", text).strip()[:MAX_DETAIL]


class FailureTally:
    """Counts failures by (exception type, normalised message).

    Deliberately not a logger: the point is the summary at the end of a stage, where a
    count and a handful of example subjects say more than a scrollback of identical lines.
    """

    def __init__(self, max_examples: int = 5) -> None:
        self._counts: Counter[tuple[str, str]] = Counter()
        self._examples: dict[tuple[str, str], list[str]] = {}
        self._messages: dict[tuple[str, str], str] = {}
        self._max_examples = max_examples

    def __len__(self) -> int:
        return sum(self._counts.values())

    def __bool__(self) -> bool:
        return bool(self._counts)

    def __iter__(self) -> Iterator[tuple[str, str, int, list[str]]]:
        for (kind, detail), count in self._counts.most_common():
            yield kind, detail, count, self._examples.get((kind, detail), [])

    def record(self, error: BaseException | str, subject: str | None = None) -> None:
        """Add one failure, optionally naming what it happened to."""
        if isinstance(error, BaseException):
            kind = type(error).__name__
            detail = normalise_detail(error)
        else:
            kind = "error"
            detail = normalise_detail(error)
        key = (kind, detail)
        self._counts[key] += 1
        if subject is not None:
            examples = self._examples.setdefault(key, [])
            if len(examples) < self._max_examples and subject not in examples:
                examples.append(subject)

    def record_issue(
        self,
        code: str,
        message: str | None = None,
        subject: str | None = None,
    ) -> None:
        """Add a provider-reported ValidationIssue.

        Grouped by `code` alone, not by message: provider messages embed the ticker
        ("Yahoo request failed for RELIANCE: 429 …"), so grouping on them would produce
        one singleton per stock and defeat the point. The first message seen for a code
        is kept as a representative sample.
        """
        key = (code, "")
        self._counts[key] += 1
        if message and key not in self._messages:
            self._messages[key] = normalise_detail(message)
        if subject is not None:
            examples = self._examples.setdefault(key, [])
            if len(examples) < self._max_examples and subject not in examples:
                examples.append(subject)

    def sample_message(self, kind: str) -> str:
        """A representative message for a code, if one was recorded."""
        return self._messages.get((kind, ""), "")

    def summary(self, limit: int = 3) -> str:
        """One line naming the loudest kinds and how often each occurred.

        Empty string when nothing failed, so callers can use it directly in a condition.
        """
        if not self._counts:
            return ""
        parts = []
        for (kind, detail), count in self._counts.most_common(limit):
            text = detail or self._messages.get((kind, detail), "")
            parts.append(f"{count}x {kind}" + (f" ({text})" if text else ""))
        remaining = len(self._counts) - limit
        if remaining > 0:
            parts.append(f"and {remaining} other kind(s)")
        return "; ".join(parts)

    def as_dict(self) -> list[dict[str, object]]:
        """Machine-readable form for storing alongside a job record."""
        return [
            {
                "kind": kind,
                "detail": detail or self._messages.get((kind, detail), ""),
                "count": count,
                "examples": examples,
            }
            for kind, detail, count, examples in self
        ]
