"""Fixed-window throttles; callers commit all consumed buckets before refusal."""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class ThrottleResult:
    allowed: bool
    retry_after: int


class AuthThrottleRepository:
    WINDOW_SECONDS = 900
    CAPACITY = 10000
    CLEANUP_LIMIT = 100

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._cleaned = False

    def consume(self, bucket_type: str, bucket_key: str, *, limit: int, now: datetime) -> ThrottleResult:
        if bucket_type not in ('login','token','source','password'):
            raise ValueError('unknown throttle bucket type')
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError('limit must be a positive integer')
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('now must be timezone-aware')
        now = now.astimezone(timezone.utc)
        cutoff = (now-timedelta(seconds=self.WINDOW_SECONDS)).isoformat()
        if not self._cleaned:
            self.connection.execute('DELETE FROM auth_throttles WHERE rowid IN (SELECT rowid FROM auth_throttles WHERE window_started_at<=? ORDER BY window_started_at LIMIT ?)', (cutoff,self.CLEANUP_LIMIT))
            self._cleaned = True
        row = self.connection.execute('SELECT window_started_at,attempts FROM auth_throttles WHERE bucket_type=? AND bucket_key=?', (bucket_type,bucket_key)).fetchone()
        if row is not None:
            started = datetime.fromisoformat(row['window_started_at'])
            if started + timedelta(seconds=self.WINDOW_SECONDS) > now:
                self.connection.execute('UPDATE auth_throttles SET attempts=attempts+1 WHERE bucket_type=? AND bucket_key=?', (bucket_type,bucket_key))
                allowed = row['attempts'] < limit
                return ThrottleResult(allowed,0 if allowed else max(1,math.ceil((started+timedelta(seconds=self.WINDOW_SECONDS)-now).total_seconds())))
        # Both a new key and an expired key need a free active bucket slot.
        count = self.connection.execute('SELECT COUNT(*) FROM auth_throttles WHERE window_started_at>?', (cutoff,)).fetchone()[0]
        if count >= self.CAPACITY:
            earliest = self.connection.execute('SELECT MIN(window_started_at) FROM auth_throttles WHERE window_started_at>?', (cutoff,)).fetchone()[0]
            retry = math.ceil((datetime.fromisoformat(earliest)+timedelta(seconds=self.WINDOW_SECONDS)-now).total_seconds())
            return ThrottleResult(False,max(1,retry))
        if row is not None:
            self.connection.execute('UPDATE auth_throttles SET window_started_at=?,attempts=1 WHERE bucket_type=? AND bucket_key=?', (now.isoformat(),bucket_type,bucket_key))
        else:
            self.connection.execute('INSERT INTO auth_throttles VALUES (?,?,?,1)', (bucket_type,bucket_key,now.isoformat()))
        return ThrottleResult(True,0)
