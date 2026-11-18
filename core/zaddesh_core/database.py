from __future__ import annotations

from typing import Generator, cast
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from zaddesh_core.config import settings

connect_args = {}
if settings.database.url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = None if settings.database.url.startswith(("mongodb://", "mongodb+srv://")) else create_engine(
    settings.database.url,
    echo=settings.database.echo,
    connect_args=connect_args,
)


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if settings.database.url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA synchronous={settings.database.sqlite_synchronous}")
        cursor.execute(f"PRAGMA cache_size=-{settings.database.sqlite_cache_kib}")
        if settings.database.sqlite_synchronous == "NORMAL":
            cursor.execute("PRAGMA wal_autocheckpoint=8192")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


class Base(DeclarativeBase):
    def __getattribute__(self, name):
        if not name.startswith("_"):
            storage = object.__getattribute__(self, "__dict__").get("_mongo_session")
            if storage is not None:
                mapper = type(self).__mapper__
                if name in mapper.relationships and name not in self.__dict__:
                    return storage.related(self, mapper.relationships[name])
        return super().__getattribute__(name)


def SessionLocal():
    if engine is None:
        from zaddesh_core.mongo_store import MongoSession
        return cast(Session, MongoSession())
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def get_db() -> Generator[Session, None, None]:
    from fastapi import HTTPException
    from pymongo.errors import PyMongoError
    from zaddesh_core.mongo_store import StorageNotReady, WriteConflict
    db = None
    try:
        db = SessionLocal()
        yield db
    except WriteConflict:
        if db is not None:
            db.rollback()
        raise HTTPException(409, "A record changed concurrently. Retry the operation.") from None
    except PyMongoError as error:
        import logging
        if getattr(error, "code", None) == 112:
            if db is not None:
                db.rollback()
            raise HTTPException(409, "A record changed concurrently. Retry the operation.", headers={"Retry-After": "1"}) from None
        logging.getLogger(__name__).error("MongoDB operation failed: %s (code %s)", type(error).__name__, getattr(error, "code", None))
        if db is not None:
            db.rollback()
        raise HTTPException(503, "MongoDB storage is unavailable. Retry the operation.", headers={"Retry-After": "1"}) from None
    except StorageNotReady:
        raise HTTPException(503, "The database migration is still in progress") from None
    finally:
        if db is not None:
            db.close()

