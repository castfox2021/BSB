import os

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text

from .config import ROOT


def upgrade(database_url: str):
    config = AlembicConfig()
    config.set_main_option('script_location', str(ROOT / 'infra/migrations'))
    url = database_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    engine = create_engine(url, connect_args={'connect_timeout': 5})
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT pg_advisory_lock(73422003)'))
            connection.commit()
            try:
                config.attributes['connection'] = connection
                command.upgrade(config, 'head')
                connection.commit()
            finally:
                connection.execute(text('SELECT pg_advisory_unlock(73422003)'))
                connection.commit()
    finally:
        engine.dispose()


if __name__ == '__main__':
    upgrade(os.environ['DATABASE_URL'])
