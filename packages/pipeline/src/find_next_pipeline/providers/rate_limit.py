"""Upstox standard-API request windows, retained across refresh batches."""

import asyncio
import os
import time
from collections import deque


# Upstox "Other Standard APIs" (holdings, historical candles, fundamentals, financial
# statements): 50/second, 500/minute, 2000/30 minutes, documented as enforced per-API
# per-user.
#   https://upstox.com/developer/api-documentation/rate-limiting/
#
# "Per-API" reads as per-endpoint, but our own traffic says otherwise. Every 429 this
# project has seen fired at 56-58 requests inside one second while the per-minute count
# was 104 of 500 and the 30-minute count 104 of 2000 -- three endpoint windows of 45/s
# each, summing past the account's 50/s. Sharing one window brought the peak to 15/s and
# the 429s to zero. The per-second cap is the one that actually bites, and it is shared.
#
# Overridable because these are Upstox's numbers, not ours, and they change: set
# UPSTOX_RATE_LIMITS="50,500,2000" to retune without a code edit. Lower the first number
# if 429s reappear; raising the third is what shortens a long backfill, and is also what
# risks the "temporary suspension of access" the docs warn about.
def _limits() -> tuple[tuple[int, int], ...]:
    raw = os.environ.get("UPSTOX_RATE_LIMITS", "")
    if raw:
        per_second, per_minute, per_half_hour = (int(x) for x in raw.split(","))
        return ((per_second, 1), (per_minute, 60), (per_half_hour, 1800))
    return ((50, 1), (500, 60), (2000, 1800))


LIMITS = _limits()


class RequestWindows:
    def __init__(self):
        self.requests = deque()

    def delay(self, now):
        while self.requests and now - self.requests[0] >= 1800:
            self.requests.popleft()
        return max(
            [0]
            + [
                self.requests[-limit] + window - now
                for limit, window in LIMITS
                if len(self.requests) >= limit
            ]
        )

    async def acquire(self):
        # Caller serializes this method with a lock local to its event loop.
        delay = self.delay(time.monotonic())
        if delay > 0:
            await asyncio.sleep(delay)
        self.requests.append(time.monotonic())


# Upstox meters per account, not per endpoint or per provider, so every caller has to
# draw from one budget. Three providers each holding a private RequestWindows, and
# upstox_statements holding one per endpoint on top of that, let a single run issue
# several times the permitted rate: a full pass produced 30 HTTP 429s against
# balance-sheet / cash-flow / income-statement, and the statements stage finished with
# data for 37 of 5,428 stocks.
#
# Module-level because the three Upstox stages are constructed separately, one per
# refresh stage, and must still share a budget. Timestamps only -- nothing here is bound
# to an event loop, so it survives the asyncio.run() that each batch starts.
#
# ponytail: process-global. Correct for one API process; two processes refreshing the
# same Upstox account would each think they own the whole budget.
ACCOUNT_WINDOW = RequestWindows()
