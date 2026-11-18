"""Compile the core's relational model expressions into native MongoDB pipelines.

Declarative models provide field metadata. Reads execute in MongoDB, including
joins, grouped aggregates, correlated subqueries and time-series window selection.
No SQL engine or local database is used by this backend.
"""
from __future__ import annotations

import operator
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.sql import operators, visitors
from sqlalchemy.sql.elements import (
    BindParameter, BooleanClauseList, Case, Cast, ClauseList, ColumnClause, Extract,
    ExpressionClauseList, False_, Grouping, Label, Null, Over, True_, TypeCoerce, UnaryExpression,
)
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.sql.selectable import Exists, Join, ScalarSelect, Select


def encode(value: Any) -> Any:
    if isinstance(value, datetime):
        at = value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
        delta = at - datetime(1970, 1, 1)
        return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    if isinstance(value, (tuple, list, set)):
        return [encode(item) for item in value]
    return value


def literal(value: Any) -> dict[str, Any]:
    return {"$literal": encode(value)}


def base_table(source):
    while hasattr(source, "original"):
        source = source.original
    return source


def inspect_entity(expr):
    info = inspect(expr, raiseerr=False)
    if info is not None and (getattr(info, "is_mapper", False) or getattr(info, "is_aliased_class", False)):
        return info.mapper if getattr(info, "is_aliased_class", False) else info
    return None


class Pipeline:
    def __init__(self, statement: Select, outer: dict[Any, str] | None = None):
        self.statement = statement
        self.sources: dict[Any, str] = dict(outer or {})
        self.stages: list[dict[str, Any]] = []
        self.groups: dict[str, str] = {}
        self.aggregates: dict[int, str] = {}
        self.windows: dict[int, str] = {}
        self.collection = ""
        self.root = None
        self.keys = list(statement.selected_columns.keys())

    def path(self, source, column: str) -> str:
        prefix = self.sources.get(source)
        if prefix is None:
            # Annotated ORM tables compare equal to their underlying Table.
            for known, candidate in self.sources.items():
                if known is source or known == source:
                    prefix = candidate
                    break
        if prefix is None:
            raise NotImplementedError(f"Unbound MongoDB query source {source.name}")
        return prefix + "." + column if prefix else "$" + column

    def source(self, source) -> None:
        if isinstance(source, Join):
            self.source(source.left)
            self.join(source.right, source.onclause, source.isouter)
            return
        alias = "s" + str(len(self.sources))
        if isinstance(getattr(source, "element", None), Select):
            nested = Pipeline(source.element)
            collection, stages = nested.compile()
            self.collection = collection
            self.stages.extend(stages)
        else:
            self.collection = base_table(source).name
            self.root = source
        self.stages.append({"$project": {alias: "$$ROOT", "_id": 0}})
        self.sources[source] = "$" + alias

    def lookup(self, statement: Select) -> tuple[str, list[str]]:
        name = "q" + str(len(self.stages))
        variables = {"o" + str(index): prefix or "$$ROOT" for index, prefix in enumerate(self.sources.values())}
        outer = {table: "$$o" + str(index) for index, table in enumerate(self.sources)}
        nested = Pipeline(statement, outer)
        collection, stages = nested.compile()
        self.stages.append({"$lookup": {"from": collection, "let": variables, "pipeline": stages, "as": name}})
        return "$" + name, nested.keys

    def join(self, source, condition, is_outer: bool) -> None:
        from sqlalchemy import select
        self_key = "s" + str(len(self.sources))
        if isinstance(getattr(source, "element", None), Select):
            query = source.element
            # A CTE's selected column names are retained by the nested projection.
            nested = Pipeline(query)
            collection, stages = nested.compile()
            original = dict(self.sources)
            self.sources[source] = "$$ROOT"
            variables = {"o" + str(index): value or "$$ROOT" for index, value in enumerate(original.values())}
            resolver = Pipeline(select(*source.c))
            resolver.sources = {table: "$$o" + str(index) for index, table in enumerate(original)}
            resolver.sources[source] = ""
            match = resolver.expression(condition)
            stages.extend(resolver.stages)
            stages.append({"$match": {"$expr": match}})
            self.sources = original
        else:
            original = dict(self.sources)
            variables = {"o" + str(index): value or "$$ROOT" for index, value in enumerate(original.values())}
            resolver = Pipeline(select(*source.c))
            resolver.sources = {table: "$$o" + str(index) for index, table in enumerate(original)}
            resolver.sources[source] = ""
            match = resolver.expression(condition)
            collection = base_table(source).name
            stages = [*resolver.stages, {"$match": {"$expr": match}}]
        self.stages.append({"$lookup": {"from": collection, "let": variables, "pipeline": stages, "as": self_key}})
        self.stages.append({"$unwind": {"path": "$" + self_key, "preserveNullAndEmptyArrays": is_outer}})
        self.sources[source] = "$" + self_key

    def regex(self, expression, pattern, mode: str, insensitive: bool = False, escape: str | None = None):
        if isinstance(pattern, BindParameter):
            value = str(pattern.effective_value)
            if mode == "like":
                parts = []
                escaped = False
                for char in value:
                    if escaped:
                        parts.append(re.escape(char))
                        escaped = False
                    elif escape and char == escape:
                        escaped = True
                    else:
                        parts.append(".*" if char == "%" else "." if char == "_" else re.escape(char))
                regex = "^" + "".join(parts) + "$"
            else:
                if escape:
                    value = value.replace(escape + escape, escape).replace(escape + "%", "%").replace(escape + "_", "_")
                regex = re.escape(value)
                regex = "^" + regex if mode == "starts" else regex + "$" if mode == "ends" else regex
            pattern_expr = literal(regex)
        else:
            raise NotImplementedError("Dynamic regular expressions require an explicit MongoDB predicate")
        return {"$regexMatch": {"input": {"$ifNull": [self.expression(expression), ""]}, "regex": pattern_expr, "options": "i" if insensitive else ""}}

    def expression(self, node):
        if node is None:
            return None
        if id(node) in self.aggregates:
            return "$" + self.aggregates[id(node)]
        if id(node) in self.windows:
            return "$" + self.windows[id(node)]
        if str(node) in self.groups:
            return "$_id." + self.groups[str(node)]
        if isinstance(node, BindParameter):
            return literal(node.effective_value)
        if isinstance(node, Null):
            return None
        if isinstance(node, (True_, False_)):
            return isinstance(node, True_)
        if isinstance(node, (Label, Grouping, TypeCoerce)):
            return self.expression(node.element if hasattr(node, "element") else node.clause)
        if isinstance(node, Exists):
            selected = node.element.element
            rows, _ = self.lookup(selected.limit(1))
            return {"$gt": [{"$size": rows}, 0]}
        if isinstance(node, ScalarSelect):
            rows, keys = self.lookup(node.element)
            return {"$map": {"input": rows, "as": "item", "in": "$$item." + keys[0]}}
        if isinstance(node, Cast):
            name = node.type.__class__.__name__.lower()
            target = "long" if "integer" in name else "double" if name in {"float", "numeric"} else "bool" if name == "boolean" else "string"
            return {"$convert": {"input": self.expression(node.clause), "to": target, "onError": None, "onNull": None}}
        if isinstance(node, Extract):
            if node.field == "epoch":
                return {"$divide": [self.expression(node.expr), 1_000_000]}
            raise NotImplementedError(f"Unsupported date extraction {node.field}")
        if isinstance(node, Case):
            return {"$switch": {"branches": [{"case": self.expression(test), "then": self.expression(value)} for test, value in node.whens], "default": self.expression(node.else_)}}
        if isinstance(node, BooleanClauseList):
            return {"$and" if node.operator is operators.and_ else "$or": [self.expression(part) for part in node.clauses]}
        if isinstance(node, ExpressionClauseList):
            if node.operator is operators.and_:
                return [self.expression(part) for part in node.clauses]
            functions = {operators.concat_op: "$concat", operator.add: "$add", operator.mul: "$multiply"}
            if node.operator not in functions:
                raise NotImplementedError(f"Unsupported MongoDB clause operation {node.operator}")
            return {functions[node.operator]: [self.expression(part) for part in node.clauses]}
        if isinstance(node, ClauseList):
            return [self.expression(part) for part in node.clauses]
        if isinstance(node, UnaryExpression):
            if node.operator in (operators.inv, operators.is_false):
                return {"$not": [self.expression(node.element)]}
            if node.operator is operators.is_true:
                return {"$eq": [self.expression(node.element), True]}
            return self.expression(node.element)
        if isinstance(node, FunctionElement):
            name = node.name.lower()
            arguments = [self.expression(part) for part in node.clauses]
            if name == "lower":
                return {"$toLower": arguments[0]}
            if name == "trim":
                return {"$trim": {"input": {"$ifNull": [arguments[0], ""]}}}
            if name == "coalesce":
                return {"$ifNull": arguments}
            if name == "strftime" and list(node.clauses)[0].effective_value == "%s":
                return {"$divide": [arguments[1], 1_000_000]}
            raise NotImplementedError(f"Unsupported MongoDB scalar function {name}")
        if isinstance(node, ColumnClause):
            if node.name == "*":
                return 1
            return self.path(node.table, node.name)
        if hasattr(node, "left") and hasattr(node, "right"):
            op = node.operator
            comparisons = {operators.eq: "$eq", operators.ne: "$ne", operators.gt: "$gt", operators.ge: "$gte", operators.lt: "$lt", operators.le: "$lte", operators.is_: "$eq", operators.is_not: "$ne"}
            if op in comparisons:
                left, right = self.expression(node.left), self.expression(node.right)
                if op in (operators.is_, operators.is_not):
                    left = {"$ifNull": [left, None]}
                return {comparisons[op]: [left, right]}
            patterns = {operators.like_op: ("like", False), operators.ilike_op: ("like", True), operators.contains_op: ("contains", False), operators.icontains_op: ("contains", True), operators.startswith_op: ("starts", False), operators.istartswith_op: ("starts", True), operators.endswith_op: ("ends", False), operators.iendswith_op: ("ends", True)}
            if op in patterns:
                mode, insensitive = patterns[op]
                return self.regex(node.left, node.right, mode, insensitive, node.modifiers.get("escape"))
            if op in (operators.not_like_op, operators.not_ilike_op):
                return {"$not": [self.regex(node.left, node.right, "like", op is operators.not_ilike_op, node.modifiers.get("escape"))]}
            left, right = self.expression(node.left), self.expression(node.right)
            if op in (operators.in_op, operators.not_in_op):
                value = {"$in": [left, right]}
                return {"$not": [value]} if op is operators.not_in_op else value
            if op in (operators.between_op, operators.not_between_op):
                value = {"$and": [{"$gte": [left, right[0]]}, {"$lte": [left, right[1]]}]}
                return {"$not": [value]} if op is operators.not_between_op else value
            if op is operators.json_getitem_op:
                key = node.right.effective_value
                return {"$getField": {"input": left, "field": literal(str(key))}}
            if op is operators.concat_op:
                return {"$concat": [left, right]}
            arithmetic = {operator.add: "$add", operator.sub: "$subtract", operator.mul: "$multiply", operator.truediv: "$divide", operator.mod: "$mod"}
            if op in arithmetic:
                return {arithmetic[op]: [left, right]}
            if op is operator.floordiv:
                return {"$floor": {"$divide": [left, right]}}
            raise NotImplementedError(f"Unsupported MongoDB expression operator {op}")
        raise NotImplementedError(f"Unsupported MongoDB expression {type(node).__name__}")

    def compile(self) -> tuple[str, list[dict[str, Any]]]:
        statement = self.statement
        sources = [source for source in statement.get_final_froms() if source not in self.sources]
        if not sources:
            raise NotImplementedError("MongoDB queries require a collection")
        self.source(sources[0])
        for source in sources[1:]:
            self.join(source, True_(), False)
        if self.root is not None:
            early = []
            def predicates(node):
                if isinstance(node, BooleanClauseList) and node.operator is operators.and_:
                    return [item for part in node.clauses for item in predicates(part)]
                return [node]
            for criterion in statement._where_criteria:
                for node in predicates(criterion):
                    if hasattr(node, "left") and isinstance(node.left, ColumnClause) and node.left.table == self.root:
                        field = node.left.name
                        if isinstance(node.right, BindParameter):
                            comparison = {operators.eq: "$eq", operators.ne: "$ne", operators.gt: "$gt", operators.ge: "$gte", operators.lt: "$lt", operators.le: "$lte", operators.in_op: "$in", operators.not_in_op: "$nin", operators.is_: "$eq", operators.is_not: "$ne"}.get(node.operator)
                            if comparison:
                                early.append({field: {comparison: encode(node.right.effective_value)}})
                                continue
                        if node.operator is operators.between_op and all(isinstance(part, BindParameter) for part in node.right.clauses):
                            lower, upper = node.right.clauses
                            early.append({field: {"$gte": encode(lower.effective_value), "$lte": encode(upper.effective_value)}})
                            continue
                    references = [item.table for item in visitors.iterate(node) if isinstance(item, ColumnClause) and item.table is not None]
                    if references and all(table == self.root or table in self.sources and self.sources[table].startswith("$$") for table in references) and not any(isinstance(item, (Exists, ScalarSelect)) for item in visitors.iterate(node)):
                        resolver = Pipeline(statement)
                        resolver.sources = {**self.sources, self.root: ""}
                        early.append({"$expr": resolver.expression(node)})
            if early:
                self.stages.insert(0, {"$match": {"$and": early}})
        if statement._where_criteria:
            predicate = {"$and": [self.expression(value) for value in statement._where_criteria]}
            self.stages.append({"$match": {"$expr": predicate}})
        selected = list(statement.selected_columns)
        aggregates = [item for expr in selected + list(statement._order_by_clauses) for item in visitors.iterate(expr)
                      if isinstance(item, FunctionElement) and item.name.lower() in {"count", "min", "max", "sum", "avg"}]
        if statement._group_by_clauses or aggregates:
            group_values = list(statement._group_by_clauses)
            grouped: dict[str, Any] = {"_id": {"g" + str(index): self.expression(value) for index, value in enumerate(group_values)}}
            for index, item in enumerate(aggregates):
                name = "a" + str(index)
                arguments = list(item.clauses)
                if item.name.lower() == "count":
                    argument = arguments[0] if arguments else None
                    count_value = 1 if argument is None or getattr(argument, "name", None) == "*" else {"$cond": [{"$ne": [{"$ifNull": [self.expression(argument), None]}, None]}, 1, 0]}
                    grouped[name] = {"$sum": count_value}
                else:
                    grouped[name] = {"$" + item.name.lower(): self.expression(arguments[0])}
                self.aggregates[id(item)] = name
            self.groups = {str(value): "g" + str(index) for index, value in enumerate(group_values)}
            self.stages.append({"$group": grouped})
        windows = [item for expr in selected for item in visitors.iterate(expr) if isinstance(item, Over)]
        for index, window in enumerate(windows):
            partition = {"p" + str(at): self.expression(value) for at, value in enumerate((window.partition_by.clauses if window.partition_by is not None else ()))}
            computed = {}
            for at, value in enumerate((window.order_by.clauses if window.order_by is not None else ())):
                descending = isinstance(value, UnaryExpression) and value.modifier is operators.desc_op
                sort_expression = value.element if isinstance(value, UnaryExpression) else value
                key = "sort" + str(index) + "x" + str(at)
                value_expression = self.expression(sort_expression)
                # Current ranking keys are numeric or microsecond timestamps.
                computed[key] = {"$multiply": [value_expression, -1]} if descending else value_expression
            name = "window" + str(index)
            sort_key = "windowSort" + str(index)
            self.stages.append({"$set": {sort_key: computed}})
            self.stages.append({"$setWindowFields": {"partitionBy": partition, "sortBy": {sort_key: 1}, "output": {name: {"$documentNumber": {}}}}})
            self.windows[id(window)] = name
        if statement._order_by_clauses:
            computed, sorts = {}, {}
            for at, value in enumerate(statement._order_by_clauses):
                descending = isinstance(value, UnaryExpression) and value.modifier is operators.desc_op
                sort_expression = value.element if isinstance(value, UnaryExpression) else value
                key = "order" + str(at)
                computed[key] = self.expression(sort_expression)
                sorts[key] = -1 if descending else 1
            self.stages.extend([{"$set": computed}, {"$sort": sorts}])
        projection = {key: self.expression(value) for key, value in zip(self.keys, selected)}
        offset = 0
        for index, description in enumerate(statement.column_descriptions):
            mapper = inspect_entity(description["expr"])
            if mapper is not None:
                projection["version" + str(index)] = {"$ifNull": [self.path(selected[offset].table, "_version"), 0]}
                offset += len(mapper.columns)
            else:
                offset += 1
        self.stages.append({"$project": {"_id": 0, **projection}})
        if statement._distinct:
            self.stages.extend([{"$group": {"_id": "$$ROOT"}}, {"$replaceWith": "$_id"}])
        if statement._offset_clause is not None:
            skip = statement._offset_clause.value
            if skip:
                self.stages.append({"$skip": skip})
        if statement._limit_clause is not None:
            limit = statement._limit_clause.value
            self.stages.append({"$limit": limit}) if limit else self.stages.append({"$match": {"$expr": False}})
        return self.collection, self.stages
