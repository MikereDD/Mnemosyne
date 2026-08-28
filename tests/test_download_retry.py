from mnemosyne.fetcher import _is_retryable_status, _retry_delay


def test_archive_transient_statuses_are_retryable() -> None:
    for status in (408, 425, 429, 500, 502, 503, 504):
        assert _is_retryable_status(status) is True


def test_permanent_http_errors_are_not_retried() -> None:
    for status in (400, 401, 403, 404, 410):
        assert _is_retryable_status(status) is False


def test_retry_backoff_is_bounded() -> None:
    assert [_retry_delay(i) for i in range(1, 7)] == [
        1.0,
        2.0,
        4.0,
        8.0,
        8.0,
        8.0,
    ]
