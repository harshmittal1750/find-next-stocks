import asyncio
import base64

import httpx
import pytest
from find_next_pipeline.providers.http import ArchivedHttpClient
from find_next_pipeline.providers.rate_limit import LIMITS, RequestWindows
from find_next_pipeline.raw_store import RawJsonStore


@pytest.mark.parametrize("status,body", [(200, b'{"value": 3}'), (502, b"bad gateway")])
def test_body_is_archived_before_decoding_even_on_errors(tmp_path, status, body):
    store = RawJsonStore(tmp_path)
    client = ArchivedHttpClient(store)

    class Response(httpx.Response):
        def json(self, **kwargs):
            assert len(store.saved) == 1
            return super().json(**kwargs)

    async def get(*args):
        return Response(status, content=body, request=httpx.Request("GET", "https://test/api"))

    client._get = get
    call = client.get_json(provider="test", endpoint="https://test/api")
    if status >= 400:
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(call)
    else:
        assert asyncio.run(call)[1] == {"value": 3}
    envelope, path = store.saved[0]
    assert base64.b64decode(envelope.payload["body_base64"]) == body
    assert envelope.status_code == status and path.exists()


def test_request_windows_enforce_long_limit_across_batches():
    """The 30-minute budget, read from LIMITS rather than restated here.

    Hard-coding 1900 meant this test disagreed with the limiter the moment the numbers
    were corrected to Upstox's documented 2000.
    """
    count, window = LIMITS[2]
    limiter = RequestWindows()
    # Spread one full budget over more than the window: nothing is owed.
    limiter.requests.extend(range(count))
    assert limiter.delay(count) == 0
    # Same budget packed into half the window: the oldest must age out first.
    limiter.requests.clear()
    limiter.requests.extend(i / 2 for i in range(count))
    assert limiter.delay(count / 2) == window - count / 2
    assert limiter.delay(window) == 0
