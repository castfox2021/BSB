import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .config import STREAM_TYPES, Config


@dataclass
class Shard:
    symbols: list[str]
    connection_id: str = ''
    connected: bool = False
    last_message: float = 0
    reconnects: int = 0
    last_error: str | None = None
    last_lag_seconds: float | None = None
    seen: dict[str, dict[str, tuple[float, str]]] = field(default_factory=dict)


class State:
    def __init__(self, config: Config, clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.clock = clock
        self.markets: list[dict] = []
        self.refreshed_at: str | None = None
        self.refreshed_mono: float | None = None
        self.rest_error: str | None = None
        self.shards: list[Shard] = []
        self.counts: Counter = Counter()
        self.recent: OrderedDict[str, None] = OrderedDict()
        self.running = False

    def reset(self, shard: Shard, connection_id: str):
        shard.connection_id = connection_id
        shard.connected = True
        shard.last_message = 0
        shard.seen.clear()
        shard.last_error = None
        shard.last_lag_seconds = None

    def disconnect(self, shard: Shard):
        shard.connected = False
        shard.seen.clear()

    def acknowledge(self, shard: Shard, event: dict):
        self.recent[event['event_id']] = None
        self.recent.move_to_end(event['event_id'])
        if len(self.recent) > 50_000:
            self.recent.popitem(last=False)
        now = self.clock()
        shard.last_message = now
        lag = (datetime.fromisoformat(event['received_at'].replace('Z', '+00:00'))
               - datetime.fromisoformat(event['exchange_at'].replace('Z', '+00:00'))).total_seconds()
        shard.last_lag_seconds = lag
        if -1 <= lag <= self.config.stale_seconds:
            shard.seen.setdefault(event['symbol'], {})[event['event_type'].split('.')[1]] = (now, event['stream_type'])
        else:
            shard.seen.setdefault(event['symbol'], {}).pop(event['event_type'].split('.')[1], None)
            self.counts['clock_skew_events' if lag < -1 else 'stale_events'] += 1
        self.counts[event['event_type']] += 1
        self.counts[f"{event['event_type']}.{event['stream_type']}"] += 1

    def status(self) -> dict:
        now = self.clock()
        fresh_symbols = []
        for shard in self.shards:
            if not shard.connected:
                continue
            for symbol, streams in shard.seen.items():
                if all(kind in streams and now - streams[kind][0] <= self.config.stale_seconds
                       and streams[kind][1] == 'REALTIME' for kind in STREAM_TYPES):
                    fresh_symbols.append(symbol)
        metadata_fresh = (self.refreshed_mono is not None
                          and now - self.refreshed_mono <= self.config.refresh_seconds * 2)
        transport_ok = (bool(self.shards) and metadata_fresh and all(
            s.connected and bool(s.last_message) and now - s.last_message <= self.config.stale_seconds
            for s in self.shards))
        return {'source': self.config.source, 'mode': 'paper', 'entries_allowed': False,
                'history_complete': False, 'running': self.running,
                'transport_ready': transport_ok, 'markets_count': len(self.markets),
                'markets_refreshed_at': self.refreshed_at, 'metadata_fresh': metadata_fresh,
                'rest_error': self.rest_error, 'fresh_symbols': sorted(fresh_symbols),
                'counts': dict(self.counts),
                'clock_skew_detected': any(s.connected and s.last_lag_seconds is not None
                                           and s.last_lag_seconds < -1 for s in self.shards),
                'shards': [{'symbols_count': len(s.symbols), 'connected': s.connected,
                            'connection_id': s.connection_id, 'reconnects': s.reconnects,
                            'last_error': s.last_error,
                            'last_lag_seconds': s.last_lag_seconds,
                            'age_seconds': round(now - s.last_message, 3) if s.last_message else None}
                           for s in self.shards]}
