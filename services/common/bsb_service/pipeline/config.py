import os
from dataclasses import dataclass
from pathlib import Path


def _resolve_project_root() -> Path:
    if env_root := os.getenv('BSB_PROJECT_ROOT'):
        return Path(env_root).resolve()
    cur = Path(__file__).resolve()
    for parent in [cur, *cur.parents]:
        if (parent / 'packages/contracts/market-trade.schema.json').exists():
            return parent
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / 'packages/contracts/market-trade.schema.json').exists():
            return parent
    return cur.parents[4] if len(cur.parents) > 4 else cur.parent


ROOT = _resolve_project_root()


@dataclass(frozen=True)
class Config:
    source: str = 'mock'
    database_url: str = ''
    redis_url: str = 'redis://redis:6379/0'
    batch_size: int = 200
    lateness_seconds: int = 40
    max_delay_seconds: int = 15
    heartbeat_timeout: int = 15

    def __post_init__(self):
        if self.source not in ('mock', 'bithumb_public'):
            raise ValueError('PIPELINE_SOURCE must be mock or bithumb_public')
        for key, lo, hi in [('batch_size', 1, 1000), ('lateness_seconds', 0, 300),
                            ('max_delay_seconds', 1, 300), ('heartbeat_timeout', 10, 120)]:
            if not lo <= getattr(self, key) <= hi:
                raise ValueError(f'invalid {key}')

    @property
    def stream(self):
        return f'bsb:{self.source}:events'

    @classmethod
    def from_env(cls):
        return cls(source=os.getenv('PIPELINE_SOURCE', 'mock'),
                   database_url=os.environ['DATABASE_URL'], redis_url=os.environ['REDIS_URL'],
                   batch_size=int(os.getenv('PIPELINE_BATCH_SIZE', '200')),
                   lateness_seconds=int(os.getenv('PIPELINE_LATENESS_SECONDS', '40')),
                   max_delay_seconds=int(os.getenv('PIPELINE_MAX_DELAY_SECONDS', '15')),
                   heartbeat_timeout=int(os.getenv('PIPELINE_HEARTBEAT_TIMEOUT', '15')))
