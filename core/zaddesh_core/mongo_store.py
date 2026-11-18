"""Native MongoDB persistence for the core's existing model representations."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from pymongo import ASCENDING, IndexModel, MongoClient, ReturnDocument, UpdateOne
from pymongo.errors import CollectionInvalid, BulkWriteError, DuplicateKeyError, OperationFailure
from sqlalchemy import DateTime, Integer, UniqueConstraint, delete, inspect, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Query, attributes
from sqlalchemy.sql.dml import Delete, Update
from sqlalchemy.sql.elements import TextClause

from zaddesh_core.config import settings
from zaddesh_core.mongo_queries import Pipeline, encode, inspect_entity


class WriteConflict(SQLAlchemyError):
    pass


class StorageNotReady(SQLAlchemyError):
    pass


@lru_cache(maxsize=1)
def mongo_database():
    client: MongoClient = MongoClient(settings.database.url, serverSelectionTimeoutMS=5000, timeoutMS=20000)
    return client.get_default_database(default=settings.database.name)


def document_id(table, values: dict[str, Any]):
    keys = [values[column.name] for column in table.primary_key.columns]
    if len(keys) == 1:
        return keys[0]
    return hashlib.sha256(json.dumps(keys, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def decode(column, value):
    if value is not None and isinstance(column.type, DateTime):
        return datetime(1970, 1, 1) + timedelta(microseconds=int(value))
    return value


def model_document(obj) -> dict[str, Any]:
    mapper = inspect(type(obj))
    result = {column.name: encode(getattr(obj, column.key)) for column in mapper.columns}
    result["_id"] = document_id(mapper.local_table, result)
    if mapper.local_table.name == "events" and obj.timestamp is not None:
        expiry = obj.timestamp + timedelta(days=settings.logs.retention_days)
        result["expires_at"] = expiry.replace(microsecond=expiry.microsecond // 1000 * 1000)
    return result


@lru_cache(maxsize=1)
def ensure_schema() -> None:
    from zaddesh_core.database import Base
    database = mongo_database()
    hello = database.client.admin.command("hello")
    if not hello.get("setName") and hello.get("msg") != "isdbgrid":
        raise RuntimeError("MongoDB must run as a replica set or sharded cluster to support core transactions")
    for table in Base.metadata.sorted_tables:
        try:
            database.create_collection(table.name)
        except CollectionInvalid:
            pass
        collection = database[table.name]
        specs: dict[tuple[str, ...], bool] = {}
        for column in table.columns:
            if column.index or column.unique or column.primary_key:
                specs[(column.name,)] = bool(column.unique or len(table.primary_key.columns) == 1 and column.primary_key)
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                specs[tuple(column.name for column in constraint.columns)] = True
        for index in table.indexes:
            keys = tuple(column.name for column in index.columns)
            specs[keys] = specs.get(keys, False) or bool(index.unique)
        if len(table.primary_key.columns) > 1:
            specs[tuple(column.name for column in table.primary_key.columns)] = True
        index_models = []
        for keys, unique in specs.items():
            options: dict[str, Any] = {"name": "idx_" + "_".join(keys), "unique": unique}
            nullable = [table.c[key] for key in keys if table.c[key].nullable]
            if unique and nullable:
                options["partialFilterExpression"] = {column.name: {"$type": "string"} for column in nullable}
            index_models.append(IndexModel([(key, ASCENDING) for key in keys], **options))
        if table.name == "events":
            index_models.append(IndexModel("expires_at", expireAfterSeconds=0, name="event_log_retention"))
        if table.name == "metrics_raw":
            index_models.append(IndexModel([("device_id", 1), ("metric_key", 1), ("timestamp", -1)], name="latest_device_metric"))
        if index_models:
            import pymongo
            with pymongo.timeout(1800):
                collection.create_indexes(index_models)
    database["_schema"].update_one({"_id": "core"}, {"$set": {"version": 1, "datetime_encoding": "utc_microseconds"}}, upsert=True)


class Row(tuple):
    def __new__(cls, values, keys):
        instance = super().__new__(cls, values)
        instance.keys = keys
        return instance

    def __getattr__(self, name):
        if name in self.keys:
            return self[self.keys.index(name)]
        raise AttributeError(name)


class Result:
    def __init__(self, rows=(), rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar(self):
        row = self.first()
        return row[0] if row is not None else None

    def scalars(self):
        return ScalarResult([row[0] for row in self.rows])


class ScalarResult(Result):
    def scalar(self):
        return self.first()


class MongoQuery:
    def __init__(self, storage, query):
        self.storage = storage
        self.query = query

    def __getattr__(self, name):
        method = getattr(self.query, name)

        def chained(*args, **kwargs):
            result = method(*args, **kwargs)
            return MongoQuery(self.storage, result) if isinstance(result, Query) else result
        return chained

    def __iter__(self):
        return iter(self.all())

    def all(self):
        result = self.storage.execute(self.query.statement, populate_existing=self.query.load_options._populate_existing).all()
        if self.query._for_update_arg is not None:
            self.storage.lock_records([value for row in result for value in row if hasattr(type(value), "__mapper__")])
            result = self.storage.execute(self.query.statement, populate_existing=True).all()
        descriptions = self.query.column_descriptions
        if len(descriptions) == 1 and inspect_entity(descriptions[0]["expr"]) is not None:
            return [row[0] for row in result]
        return result

    def first(self):
        rows = self.limit(1).all()
        return rows[0] if rows else None

    def scalar(self):
        row = self.first()
        return row[0] if isinstance(row, tuple) else row

    def one_or_none(self):
        from sqlalchemy.exc import MultipleResultsFound
        rows = self.limit(2).all()
        if len(rows) > 1:
            raise MultipleResultsFound("Multiple records matched")
        return rows[0] if rows else None

    def one(self):
        from sqlalchemy.exc import NoResultFound
        row = self.one_or_none()
        if row is None:
            raise NoResultFound("No record matched")
        return row

    def count(self):
        compiler = Pipeline(self.query.statement)
        collection, stages = compiler.compile()
        rows = list(self.storage.database[collection].aggregate([*stages, {"$count": "count"}], session=self.storage.read_session, maxTimeMS=15000))
        return rows[0]["count"] if rows else 0

    def delete(self, synchronize_session=None):
        table = self.query.column_descriptions[0]["entity"].__table__
        return self.storage.execute(delete(table).where(*self.query.statement._where_criteria)).rowcount

    def update(self, values, synchronize_session=None):
        table = self.query.column_descriptions[0]["entity"].__table__
        return self.storage.execute(update(table).where(*self.query.statement._where_criteria).values(values)).rowcount


class MongoSession:
    def __init__(self, database=None, initialize=True):
        self.database = database if database is not None else mongo_database()
        if initialize:
            ensure_schema()
            if self.database["_migration"].find_one({"_id": "sql", "state": {"$ne": "verified"}}):
                raise StorageNotReady("Complete the verified database migration before starting the core")
        self.transaction = None
        self.identity: dict[tuple[type, Any], Any] = {}
        self.snapshots: dict[int, dict[str, Any]] = {}
        self.versions: dict[int, int] = {}
        self.pending: list[Any] = []
        self.reference_guards: set[tuple[str, str, Any]] = set()

    @property
    def read_session(self):
        return self.transaction

    def write_session(self):
        if self.transaction is None:
            self.transaction = self.database.client.start_session()
            self.transaction.start_transaction()
        return self.transaction

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def query(self, *entities):
        return MongoQuery(self, Query(entities))

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="mongodb"))

    def ping(self):
        self.database.command("ping")

    def next_id(self, table: str, amount: int = 1) -> int:
        row = self.database["_counters"].find_one_and_update({"_id": table}, {"$inc": {"value": amount}}, upsert=True, return_document=ReturnDocument.AFTER)
        return row["value"] - amount + 1

    def hydrate(self, model, document, refresh_existing=False):
        table = inspect(model).local_table
        if not document or any(document.get(column.name) is None for column in table.primary_key.columns):
            return None
        identifier = document_id(table, document)
        key = (model, identifier)
        if key in self.identity:
            obj = self.identity[key]
            if refresh_existing:
                for column in table.columns:
                    setattr(obj, column.key, decode(column, document.get(column.name)))
                for relationship in inspect(model).relationships:
                    obj.__dict__.pop(relationship.key, None)
                self.snapshots[id(obj)] = copy.deepcopy(model_document(obj))
                self.versions[id(obj)] = document.get("_version", 0)
            return obj
        obj = model(**{column.key: decode(column, document.get(column.name)) for column in table.columns})
        obj._mongo_session = self
        self.identity[key] = obj
        self.snapshots[id(obj)] = copy.deepcopy(model_document(obj))
        self.versions[id(obj)] = document.get("_version", 0)
        return obj

    def get(self, model, identifier):
        mapper = inspect(model)
        values = identifier if isinstance(identifier, tuple) else (identifier,)
        fields = {column.name: value for column, value in zip(mapper.primary_key, values)}
        key = (model, document_id(mapper.local_table, fields))
        if key in self.identity:
            return self.identity[key]
        document = self.database[mapper.local_table.name].find_one({"_id": key[1]}, session=self.read_session)
        return self.hydrate(model, document)

    def related(self, obj, relationship):
        model = relationship.mapper.class_
        clauses = []
        for local, remote in relationship.local_remote_pairs:
            value = getattr(obj, local.key)
            if value is None:
                result = [] if relationship.uselist else None
                attributes.set_committed_value(obj, relationship.key, result)
                return result
            clauses.append(getattr(model, remote.key) == value)
        query = self.query(model).filter(*clauses)
        result = query.all() if relationship.uselist else query.first()
        attributes.set_committed_value(obj, relationship.key, result)
        return result

    def add(self, obj):
        if id(obj) not in self.snapshots and not any(item is obj for item in self.pending):
            self.pending.append(obj)
            obj._mongo_session = self

    def add_all(self, objects):
        for obj in objects:
            self.add(obj)

    def bulk_save_objects(self, objects):
        self.add_all(objects)
        self.flush()

    def default(self, column, default):
        if default.is_callable:
            return default.arg(None)
        return copy.deepcopy(default.arg)

    def validate_references(self, table, document):
        for column in table.columns:
            value = document.get(column.name)
            if value is None:
                continue
            for foreign in column.foreign_keys:
                target = foreign.column
                if target.table.name == table.name and document.get(target.name) == value:
                    continue
                guard = (target.table.name, target.name, value)
                if guard in self.reference_guards:
                    continue
                result = self.database[target.table.name].update_one({target.name: value}, {"$inc": {"_write_guard": 1}}, session=self.write_session())
                if result.matched_count != 1:
                    raise IntegrityError("MongoDB foreign key reference is unavailable", {}, None)
                self.reference_guards.add(guard)

    def flush(self):
        for obj in self.pending:
            mapper = inspect(type(obj))
            for column in mapper.columns:
                if getattr(obj, column.key) is not None:
                    continue
                if column.primary_key and isinstance(column.type, Integer):
                    setattr(obj, column.key, self.next_id(mapper.local_table.name))
                elif column.default is not None:
                    setattr(obj, column.key, self.default(column, column.default))
                elif column.server_default is not None:
                    value = str(column.server_default.arg).strip("'\"")
                    setattr(obj, column.key, int(value) if isinstance(column.type, Integer) else value)
                elif not column.nullable:
                    raise IntegrityError("Required MongoDB model field is missing", {}, None)
            document = model_document(obj)
            document["_version"] = 1
            self.validate_references(mapper.local_table, document)
            try:
                self.database[mapper.local_table.name].insert_one(document, session=self.write_session())
            except DuplicateKeyError:
                raise IntegrityError("MongoDB unique constraint", {}, None) from None
            self.identity[(type(obj), document["_id"])] = obj
            self.snapshots[id(obj)] = {key: value for key, value in document.items() if key != "_version"}
            self.versions[id(obj)] = 1
        self.pending.clear()
        grouped_updates = {}
        for obj in self.identity.values():
            previous = self.snapshots[id(obj)]
            current = model_document(obj)
            changes = {key: value for key, value in current.items() if key != "_id" and value != previous.get(key)}
            if not changes:
                continue
            mapper = inspect(type(obj))
            if any(column.foreign_keys and column.name in changes for column in mapper.columns):
                self.validate_references(mapper.local_table, current)
            for column in mapper.columns:
                if column.onupdate is not None and column.name not in changes:
                    value = self.default(column, column.onupdate)
                    setattr(obj, column.key, value)
                    current[column.name] = encode(value)
                    changes[column.name] = encode(value)
            operation = UpdateOne({"_id": current["_id"], "_version": self.versions[id(obj)]}, {"$set": changes, "$inc": {"_version": 1}})
            grouped_updates.setdefault(mapper.local_table.name, []).append((operation, obj, current))
        for table, updates in grouped_updates.items():
            try:
                result = self.database[table].bulk_write([operation for operation, _, _ in updates], ordered=True, session=self.write_session())
            except (DuplicateKeyError, BulkWriteError) as error:
                if isinstance(error, DuplicateKeyError) or any(item.get("code") == 11000 for item in error.details.get("writeErrors", [])):
                    raise IntegrityError("MongoDB unique constraint", {}, None) from None
                raise
            if result.matched_count != len(updates):
                raise WriteConflict("A record changed concurrently. Retry the operation.")
            for _, obj, current in updates:
                self.versions[id(obj)] += 1
                self.snapshots[id(obj)] = copy.deepcopy(current)

    def refresh(self, obj):
        mapper = inspect(type(obj))
        document = self.database[mapper.local_table.name].find_one({"_id": model_document(obj)["_id"]}, session=self.read_session)
        if document is None:
            raise WriteConflict("The record was removed concurrently")
        for column in mapper.columns:
            setattr(obj, column.key, decode(column, document.get(column.name)))
        for relationship in mapper.relationships:
            obj.__dict__.pop(relationship.key, None)
        self.snapshots[id(obj)] = model_document(obj)
        self.versions[id(obj)] = document.get("_version", 0)

    def lock_records(self, objects):
        grouped = {}
        for obj in objects:
            table = inspect(type(obj)).local_table
            identifier = model_document(obj)["_id"]
            grouped.setdefault(table.name, {})[identifier] = self.versions[id(obj)]
        for table, versions in grouped.items():
            by_version = {}
            for identifier, version in versions.items():
                by_version.setdefault(version, []).append(identifier)
            conditions = [{"_id": {"$in": identifiers}, "_version": version} for version, identifiers in by_version.items()]
            predicate = conditions[0] if len(conditions) == 1 else {"$or": conditions}
            try:
                locked = self.database[table].update_many(predicate, {"$inc": {"_write_guard": 1}}, session=self.write_session())
            except OperationFailure as error:
                if error.code == 112:
                    raise WriteConflict("A record is being changed concurrently") from None
                raise
            if locked.matched_count != len(versions):
                raise WriteConflict("A record changed before it could be locked")

    def execute(self, statement, parameters=None, populate_existing=False):
        if isinstance(statement, (Update, Delete)):
            return self.mutate(statement)
        if isinstance(statement, TextClause):
            raise NotImplementedError("SQL text is unavailable in MongoDB mode")
        compiler = Pipeline(statement)
        collection, pipeline = compiler.compile()
        documents = self.database[collection].aggregate(pipeline, session=self.read_session, allowDiskUse=True, maxTimeMS=15000)
        descriptions = statement.column_descriptions
        rows = []
        columns = list(statement.selected_columns)
        keys = compiler.keys
        for document in documents:
            values, index = [], 0
            for position, description in enumerate(descriptions):
                mapper = inspect_entity(description["expr"])
                if mapper is not None:
                    count = len(mapper.columns)
                    projected = {column.name: document.get(keys[index + offset]) for offset, column in enumerate(mapper.columns)}
                    projected["_version"] = document.get("version" + str(position), 0)
                    values.append(self.hydrate(mapper.class_, projected, refresh_existing=populate_existing))
                    index += count
                else:
                    value = document.get(keys[index])
                    values.append(decode(columns[index], value))
                    index += 1
            rows.append(Row(values, [entry["name"] for entry in descriptions]))
        return Result(rows)

    def scalar(self, statement):
        return self.execute(statement).scalar()

    def scalars(self, statement):
        return self.execute(statement).scalars()

    def mutate(self, statement):
        table = statement.table
        compiler = Pipeline(select(*table.c))
        compiler.sources = {table: ""}
        predicate = {"$and": [compiler.expression(value) for value in statement._where_criteria]}
        if compiler.stages:
            raise NotImplementedError("Correlated predicates require a collection query before mutation")
        filters = {"$expr": predicate} if statement._where_criteria else {}
        collection = self.database[table.name]
        if isinstance(statement, Delete):
            return Result(rowcount=self.cascade_delete(table, filters))
        changes = {getattr(column, "name", column): compiler.expression(value) for column, value in statement._values.items()}
        known = list(collection.find(filters, {"_id": 1}, session=self.write_session())) if self.identity else []
        # Queue consumers use no-op SQL updates as locks. A metadata write gives
        # those operations the same write-conflict protection in MongoDB.
        result = collection.update_many(filters, [{"$set": changes}, {"$set": {"_write_guard": {"$add": [{"$ifNull": ["$_write_guard", 0]}, 1]}}}], session=self.write_session())
        for identifier in known:
            for (model, key), obj in self.identity.items():
                if model.__tablename__ == table.name and key == identifier["_id"]:
                    document = collection.find_one({"_id": key}, session=self.read_session)
                    for column in table.columns:
                        if column.name in changes:
                            setattr(obj, column.key, decode(column, document.get(column.name)))
                            self.snapshots[id(obj)][column.name] = document.get(column.name)
        return Result(rowcount=result.matched_count)

    def cascade_delete(self, table, filters):
        from zaddesh_core.database import Base
        referring = [(other, column, foreign) for other in Base.metadata.sorted_tables for column in other.columns
                     for foreign in column.foreign_keys if foreign.column.table.name == table.name]
        collection = self.database[table.name]
        if referring:
            fields = {foreign.column.name for _, _, foreign in referring}
            for document in collection.find(filters, {key: 1 for key in fields}, session=self.write_session()):
                for other, column, foreign in referring:
                    query = {column.name: document[foreign.column.name]}
                    if foreign.ondelete == "CASCADE":
                        self.cascade_delete(other, query)
                    elif foreign.ondelete == "SET NULL":
                        self.database[other.name].update_many(query, {"$set": {column.name: None}}, session=self.write_session())
                    elif self.database[other.name].find_one(query, session=self.read_session):
                        raise IntegrityError("MongoDB foreign key reference prevents deletion", {}, None)
        result = collection.delete_many(filters, session=self.write_session())
        for key, obj in list(self.identity.items()):
            if key[0].__tablename__ == table.name and collection.find_one({"_id": key[1]}, session=self.read_session) is None:
                self.identity.pop(key)
                self.snapshots.pop(id(obj), None)
                self.versions.pop(id(obj), None)
        return result.deleted_count

    def delete(self, obj):
        mapper = inspect(type(obj))
        self.cascade_delete(mapper.local_table, {"_id": model_document(obj)["_id"]})

    def insert_metrics(self, records):
        if not records:
            return 0
        first = self.next_id("metrics_raw", len(records))
        operations = []
        for offset, record in enumerate(records):
            values = {key: encode(value) for key, value in record.items()}
            values.update(id=first + offset, _id=first + offset, _version=1)
            operations.append(UpdateOne({"fingerprint": {"$eq": values["fingerprint"], "$type": "string"}}, {"$setOnInsert": values}, upsert=True))
        return self.database.metrics_raw.bulk_write(operations, ordered=True, session=self.write_session()).upserted_count

    def latest_metrics(self, device_id):
        pipeline = [
            {"$match": {"device_id": device_id}},
            {"$sort": {"metric_key": 1, "timestamp": -1}},
            {"$group": {"_id": "$metric_key", "at": {"$first": "$timestamp"}}},
            {"$lookup": {"from": "metrics_raw", "let": {"key": "$_id", "at": "$at"}, "pipeline": [
                {"$match": {"device_id": device_id, "$expr": {"$and": [{"$eq": ["$metric_key", "$$key"]}, {"$eq": ["$timestamp", "$$at"]}]}}},
                {"$project": {"_id": 0, "metric_key": 1, "value": 1, "timestamp": 1, "tags": 1, "check_id": 1}},
            ], "as": "rows"}},
            {"$unwind": "$rows"}, {"$replaceWith": "$rows"},
        ]
        from zaddesh_core.models.metrics import MetricRaw
        return [{**row, "timestamp": decode(MetricRaw.__table__.c.timestamp, row["timestamp"])}
                for row in self.database.metrics_raw.aggregate(pipeline, session=self.read_session, maxTimeMS=15000)]

    def commit(self):
        self.flush()
        if self.transaction is not None:
            self.transaction.commit_transaction()
            self.transaction.end_session()
            self.transaction = None
        self.reference_guards.clear()

    def rollback(self):
        if self.transaction is not None:
            self.transaction.abort_transaction()
            self.transaction.end_session()
            self.transaction = None
        self.pending.clear()
        self.reference_guards.clear()
        self.identity.clear()
        self.snapshots.clear()
        self.versions.clear()

    def close(self):
        self.rollback()
