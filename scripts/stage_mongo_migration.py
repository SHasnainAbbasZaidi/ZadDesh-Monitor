"""Copy and verify a consistent SQLite snapshot in a new MongoDB staging database."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from sqlalchemy import URL, MetaData, create_engine, select

BATCH_SIZE = 1000


def canonical(document):
    return json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def preserve_precision(value):
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat(timespec="microseconds")
    return value


def copy_table(connection, table, target):
    primary_key, = table.primary_key.columns
    source_hash = hashlib.sha256()
    source_count = 0
    batch = []
    for row in connection.execute(select(table).order_by(primary_key)).mappings():
        document = {key: preserve_precision(value) for key, value in row.items()}
        document["_id"] = document[primary_key.name]
        source_hash.update(canonical(document))
        source_count += 1
        batch.append(document)
        if len(batch) == BATCH_SIZE:
            target.insert_many(batch, ordered=True)
            batch.clear()
    if batch:
        target.insert_many(batch, ordered=True)
    target_count = 0
    target_hash = hashlib.sha256()
    for document in target.find().sort("_id").batch_size(BATCH_SIZE):
        target_hash.update(canonical(document))
        target_count += 1
    if target_count != source_count or target_hash.digest() != source_hash.digest():
        raise RuntimeError(f"Content verification failed for {table.name}")
    return {"count": source_count, "sha256": source_hash.hexdigest()}


def stage(snapshot, mongo, database_name):
    engine = create_engine(URL.create("sqlite", database=str(snapshot)))
    try:
        with engine.connect() as connection:
            schema = MetaData()
            schema.reflect(connection)
            if not {"users", "devices", "checks", "metrics_raw", "alembic_version"} <= schema.tables.keys():
                raise RuntimeError("The SQLite file does not contain a migrated ZadDesh schema")
            target = mongo[database_name]
            target.create_collection("_migration")
            manifest = target["_migration"]
            manifest.insert_one({"_id": "snapshot", "status": "incomplete", "started_at": datetime.now(UTC),
                                 "timestamp_encoding": "ISO 8601 with microseconds"})
            for table in sorted(schema.tables.values(), key=lambda item: item.name):
                collection = target.create_collection(table.name)
                result = copy_table(connection, table, collection)
                manifest.update_one({"_id": "snapshot"}, {"$set": {f"tables.{table.name}": result}})
                print(f"{table.name}: {result['count']} verified", flush=True)
            manifest.update_one({"_id": "snapshot"}, {"$set": {"status": "verified", "finished_at": datetime.now(UTC)}})
            print(f"Verified {len(schema.tables)} tables in {database_name}.", flush=True)
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--database", required=True, help="New database beginning with zaddesh_migration_")
    args = parser.parse_args()
    if not re.fullmatch(r"zaddesh_migration_[a-z0-9_]{1,40}", args.database):
        parser.error("Choose a dedicated zaddesh_migration_ database name")
    uri = os.environ.get("ZADDESH_MIGRATION_MONGO_URI")
    if not uri:
        parser.error("Set ZADDESH_MIGRATION_MONGO_URI")
    try:
        source = args.sqlite.resolve(strict=True)
        with MongoClient(uri, serverSelectionTimeoutMS=3000) as mongo:
            if args.database in mongo.list_database_names():
                raise RuntimeError("The staging database already exists. Choose a new name.")
            with TemporaryDirectory(prefix="zaddesh-migration-") as directory:
                snapshot = Path(directory) / "snapshot.db"
                with (
                    closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original,
                    closing(sqlite3.connect(snapshot)) as backup,
                ):
                    original.backup(backup)
                stage(snapshot, mongo, args.database)
    except PyMongoError as exc:
        parser.exit(1, f"MongoDB staging failed ({type(exc).__name__}).\n")
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"Staging failed ({type(exc).__name__}). Check the source and staging manifest.\n")


if __name__ == "__main__":
    main()
