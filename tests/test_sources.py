import pytest
import requests

from jobfeed.sources import ApiConfig, ApiSource, SourceError, sample_records


class Response:
    def __init__(self, body=None, status=200, headers=None):
        self.body, self.status_code, self.headers = body, status, headers or {}

    def json(self):
        if self.body is None:
            raise ValueError
        return self.body


class Session:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def get(self, url, headers, params, timeout):
        self.calls.append((url, headers, params))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def source(*replies, **kw):
    waits = []
    api = ApiSource(ApiConfig("test-key"), Session(*replies), sleep=waits.append, **kw)
    return api, waits


def page(n):
    return Response([{"id": i} for i in range(n)])


def fetch(api, limit=250, page_size=100):
    return list(api.fetch("data", "United States", "24h", limit, page_size))


def test_paginates_to_the_limit():
    api, _ = source(page(100), page(100), page(50))
    assert len(fetch(api)) == 250
    assert [c[2]["offset"] for c in api.session.calls] == [0, 100, 200]
    assert api.session.calls[-1][2]["limit"] == 50


def test_short_page_ends_the_feed():
    api, _ = source(page(100), page(7))
    assert len(fetch(api)) == 107


def test_never_returns_more_than_the_limit():
    api, _ = source(page(100))
    assert len(fetch(api, limit=10)) == 10


def test_rate_limit_is_retried_and_honours_retry_after():
    api, waits = source(Response(status=429, headers={"Retry-After": "7"}), page(1))
    assert len(fetch(api)) == 1
    assert waits == [7]


def test_endless_rate_limiting_gives_up():
    api, waits = source(*[Response(status=429)] * 3, max_attempts=3)
    with pytest.raises(SourceError, match="gave up after 3 attempts: HTTP 429"):
        fetch(api)
    assert waits == [2, 4]


def test_connection_errors_are_retried():
    api, _ = source(requests.ConnectionError(), requests.Timeout(), page(2))
    assert len(fetch(api)) == 2


def test_auth_failure_is_not_retried():
    api, waits = source(Response(status=401))
    with pytest.raises(SourceError, match="401"):
        fetch(api)
    assert waits == []


@pytest.mark.parametrize("body", [None, {"message": "hi"}, "text"])
def test_unexpected_body_is_an_error(body):
    api, _ = source(Response(body))
    with pytest.raises(SourceError):
        fetch(api)


def test_wrapped_and_dirty_pages_are_accepted():
    api, _ = source(Response({"data": [{"id": 1}, "junk", None]}))
    assert fetch(api) == [{"id": 1}]


def test_errors_never_contain_the_key():
    api, _ = source(Response(status=403))
    with pytest.raises(SourceError) as err:
        fetch(api)
    assert "test-key" not in str(err.value)


def test_auth_headers():
    assert ApiConfig("k").headers == {"Authorization": "Bearer k"}
    assert ApiConfig("k", rapidapi_host="h").headers == {
        "x-rapidapi-key": "k",
        "x-rapidapi-host": "h",
    }


def test_missing_key_is_explained():
    with pytest.raises(SourceError, match="JOBFEED_API_KEY"):
        ApiConfig.from_env()


def test_sample_days_overlap_and_repeat():
    assert sample_records(2) == sample_records(2)
    one, two = ({r["id"] for r in sample_records(d)} for d in (1, 2))
    assert one < two
