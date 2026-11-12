import os
from dataclasses import dataclass

REST_URL = 'https://api.bithumb.com'
WS_URL = 'wss://ws-api.bithumb.com/websocket/v1'
STREAM_TYPES = ('ticker', 'trade', 'orderbook')


@dataclass(frozen=True)
class Config:
    source: str = 'mock'
    batch_size: int = 100
    refresh_seconds: int = 300
    stale_seconds: int = 15
    receive_timeout: int = 30
    stream_maxlen: int = 100_000
    redis_url: str = 'redis://redis:6379/0'

    def __post_init__(self):
        if self.source not in ('mock', 'bithumb_public'):
            raise ValueError('COLLECTOR_SOURCE must be mock or bithumb_public')
        for name, low, high in [('batch_size', 1, 100), ('refresh_seconds', 30, 86400),
                                ('stale_seconds', 1, 300), ('receive_timeout', 5, 120),
                                ('stream_maxlen', 100, 1_000_000)]:
            if not low <= getattr(self, name) <= high:
                raise ValueError(f'{name} must be between {low} and {high}')

    @classmethod
    def from_env(cls):
        return cls(source=os.getenv('COLLECTOR_SOURCE', 'mock'),
                   batch_size=int(os.getenv('COLLECTOR_BATCH_SIZE', '100')),
                   refresh_seconds=int(os.getenv('COLLECTOR_REFRESH_SECONDS', '300')),
                   stale_seconds=int(os.getenv('COLLECTOR_STALE_SECONDS', '15')),
                   receive_timeout=int(os.getenv('COLLECTOR_RECEIVE_TIMEOUT', '30')),
                   stream_maxlen=int(os.getenv('COLLECTOR_STREAM_MAXLEN', '100000')),
                   redis_url=os.environ['REDIS_URL'])
