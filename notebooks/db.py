import os
from sqlalchemy import create_engine


def get_engine():
    """
    Return a SQLAlchemy engine connected to the Brokerdex PostgreSQL database.
    Reads DATABASE_URL from the environment, adapting driver to postgresql+psycopg.
    """
    raw_url = os.environ.get(
        'DATABASE_URL',
        'postgresql://brokerdex_user@localhost:5432/brokerdex_db'
    )
    # SQLAlchemy requires driver specification if using psycopg 3
    if raw_url.startswith('postgresql://'):
        sqlalchemy_url = raw_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    elif raw_url.startswith('postgres://'):
        sqlalchemy_url = raw_url.replace('postgres://', 'postgresql+psycopg://', 1)
    else:
        sqlalchemy_url = raw_url

    return create_engine(sqlalchemy_url)
