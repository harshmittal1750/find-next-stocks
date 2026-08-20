from __future__ import annotations

from find_next_pipeline.diagnostics import FailureTally, normalise_detail


def test_near_identical_messages_fold_into_one_kind() -> None:
    # The durations differ; the failure does not.
    assert normalise_detail("timed out after 30s") == normalise_detail("timed out after 45s")
    assert normalise_detail("GET https://a.example/x failed") == normalise_detail(
        "GET https://b.example/y failed"
    )


def test_repeated_failures_are_counted_not_overwritten() -> None:
    """The behaviour this exists to fix: one message replacing all the others."""
    tally = FailureTally()
    for ticker in ("AAA", "BBB", "CCC"):
        tally.record(TimeoutError("Too Many Requests. Retry after 30s"), subject=ticker)
    tally.record(KeyError("DELIV_PER"), subject="bhavcopy")

    kinds = {kind: count for kind, _detail, count, _examples in tally}
    assert kinds == {"TimeoutError": 3, "KeyError": 1}
    assert len(tally) == 4


def test_the_rare_failure_survives_the_common_one() -> None:
    # A schema change buried under twenty rate-limit errors must still be visible.
    tally = FailureTally()
    for index in range(20):
        tally.record(TimeoutError(f"rate limited, batch {index}"))
    tally.record(ValueError("expected columns missing"))

    summary = tally.summary()
    assert "20x TimeoutError" in summary
    assert "ValueError" in summary


def test_examples_are_capped_and_deduplicated() -> None:
    tally = FailureTally(max_examples=2)
    for ticker in ("AAA", "AAA", "BBB", "CCC", "DDD"):
        tally.record(TimeoutError("same failure"), subject=ticker)
    _kind, _detail, count, examples = next(iter(tally))
    assert count == 5
    assert examples == ["AAA", "BBB"]


def test_summary_is_empty_when_nothing_failed() -> None:
    tally = FailureTally()
    assert tally.summary() == ""
    assert not tally
    assert len(tally) == 0


def test_summary_reports_how_many_kinds_it_omitted() -> None:
    tally = FailureTally()
    for index in range(5):
        tally.record(ValueError(f"distinct failure {chr(97 + index)}"))
    assert "other kind(s)" in tally.summary(limit=2)


def test_as_dict_is_json_ready() -> None:
    tally = FailureTally()
    tally.record(TimeoutError("boom"), subject="AAA")
    payload = tally.as_dict()
    assert payload == [
        {"kind": "TimeoutError", "detail": "boom", "count": 1, "examples": ["AAA"]}
    ]


def test_plain_strings_are_accepted() -> None:
    tally = FailureTally()
    tally.record("provider returned nothing")
    kind, detail, count, _examples = next(iter(tally))
    assert (kind, detail, count) == ("error", "provider returned nothing", 1)


def test_provider_issues_group_by_code_not_message() -> None:
    """Regression: a real refresh produced 1,353 rate-limit failures and reported none.

    Providers report most failures as ValidationIssues rather than raising, and their
    messages embed the ticker. Grouping on the message would give 1,353 singletons; the
    code is what identifies the failure.
    """
    tally = FailureTally()
    for ticker in ("RELIANCE", "TCS", "INFY"):
        tally.record_issue(
            "provider_request_failed",
            f"Yahoo request failed for {ticker}: 429 Too Many Requests",
            subject=ticker,
        )

    entries = list(tally)
    assert len(entries) == 1
    kind, _detail, count, examples = entries[0]
    assert (kind, count) == ("provider_request_failed", 3)
    assert examples == ["RELIANCE", "TCS", "INFY"]
    assert "3x provider_request_failed" in tally.summary()


def test_issue_summary_includes_a_representative_message() -> None:
    tally = FailureTally()
    tally.record_issue("provider_request_failed", "429 Too Many Requests", subject="AAA")
    assert "Too Many Requests" in tally.summary()
    assert "Too Many Requests" in tally.sample_message("provider_request_failed")


def test_summary_omits_empty_parentheses_when_no_message() -> None:
    tally = FailureTally()
    tally.record_issue("bare_code")
    assert tally.summary() == "1x bare_code"
