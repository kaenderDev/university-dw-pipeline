"""Mock em memória compatível com a interface básica do PyMongo para fallback offline."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from pymongo.results import (
    DeleteResult,
    InsertManyResult,
    InsertOneResult,
    UpdateResult,
)

_MISSING = object()


def _get_nested_value(doc: dict[str, Any], dotted_key: str) -> Any:
    """Resolve chaves aninhadas com notação de ponto (ex: 'endereco.cidade')."""
    current: Any = doc
    for part in dotted_key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _set_nested_value(doc: dict[str, Any], dotted_key: str, value: Any) -> None:
    """Define um campo usando notação de ponto criando subdocumentos se necessário."""
    parts = dotted_key.split(".")
    current = doc
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = copy.deepcopy(value)


def _unset_nested_value(doc: dict[str, Any], dotted_key: str) -> None:
    """Remove um campo usando notação de ponto."""
    parts = dotted_key.split(".")
    current: Any = doc
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _matches_operator(value: Any, op: str, operand: Any) -> bool:
    """Avalia operadores de consulta estilo MongoDB ($eq, $in, $gt, $regex, etc.)."""
    if op == "$eq":
        return value is not _MISSING and value == operand
    if op == "$ne":
        return value is _MISSING or value != operand
    if op == "$in":
        return value is not _MISSING and value in operand
    if op == "$nin":
        return value is _MISSING or value not in operand
    if op == "$exists":
        return (value is not _MISSING) is bool(operand)
    if op == "$regex":
        if not isinstance(value, str):
            return False
        pattern = operand if isinstance(operand, re.Pattern) else re.compile(operand)
        return pattern.search(value) is not None
    if value is _MISSING or value is None:
        return False
    if op == "$gt":
        return value > operand
    if op == "$gte":
        return value >= operand
    if op == "$lt":
        return value < operand
    if op == "$lte":
        return value <= operand
    raise ValueError(f"Operador de filtro não suportado no mock: {op}")


def _matches_filter(doc: dict[str, Any], filter_dict: dict[str, Any] | None) -> bool:
    """Verifica se um documento satisfaz um filtro estilo MongoDB."""
    if not filter_dict:
        return True

    for key, condition in filter_dict.items():
        if key == "$and":
            if not all(_matches_filter(doc, sub) for sub in condition):
                return False
            continue
        if key == "$or":
            if not any(_matches_filter(doc, sub) for sub in condition):
                return False
            continue

        value = _get_nested_value(doc, key)
        if isinstance(condition, dict) and any(k.startswith("$") for k in condition):
            for op, operand in condition.items():
                if op == "$options":
                    continue
                if op == "$regex" and "$options" in condition and isinstance(operand, str):
                    flags = re.IGNORECASE if "i" in condition["$options"] else 0
                    operand = re.compile(operand, flags)
                if not _matches_operator(value, op, operand):
                    return False
        else:
            if value is _MISSING or value != condition:
                return False
    return True


def _apply_projection(doc: dict[str, Any], projection: dict[str, int] | None) -> dict[str, Any]:
    """Aplica projeção simples de inclusão/exclusão sobre uma cópia do documento."""
    copied = copy.deepcopy(doc)
    if not projection:
        return copied

    include_keys = [k for k, v in projection.items() if v and k != "_id"]
    exclude_keys = [k for k, v in projection.items() if not v]

    if include_keys:
        result: dict[str, Any] = {}
        if projection.get("_id", 1) and "_id" in copied:
            result["_id"] = copied["_id"]
        for key in include_keys:
            if key in copied:
                result[key] = copied[key]
        return result

    for key in exclude_keys:
        copied.pop(key, None)
    return copied


@dataclass
class _IndexSpec:
    name: str
    keys: list[tuple[str, int]]
    unique: bool = False


class InMemoryMongoCursor:
    """Cursor em memória com suporte a sort, skip, limit e iteração."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents
        self._sort_specs: list[tuple[str, int]] = []
        self._skip_count = 0
        self._limit_count: int | None = None

    def sort(self, key_or_list: str | list[tuple[str, int]], direction: int = ASCENDING) -> InMemoryMongoCursor:
        if isinstance(key_or_list, str):
            self._sort_specs = [(key_or_list, direction)]
        else:
            self._sort_specs = list(key_or_list)
        return self

    def skip(self, count: int) -> InMemoryMongoCursor:
        self._skip_count = max(0, count)
        return self

    def limit(self, count: int) -> InMemoryMongoCursor:
        self._limit_count = count if count > 0 else None
        return self

    def _materialized(self) -> list[dict[str, Any]]:
        docs = list(self._documents)
        for field, direction in reversed(self._sort_specs):
            reverse = direction == DESCENDING
            docs.sort(
                key=lambda d: (
                    _get_nested_value(d, field) is _MISSING,
                    _get_nested_value(d, field) if _get_nested_value(d, field) is not _MISSING else "",
                ),
                reverse=reverse,
            )
        if self._skip_count:
            docs = docs[self._skip_count :]
        if self._limit_count is not None:
            docs = docs[: self._limit_count]
        return docs

    def __iter__(self):
        return iter(self._materialized())

    def __len__(self) -> int:
        return len(self._materialized())


class InMemoryMongoCollection:
    """Coleção MongoDB em memória com índices únicos e operações CRUD."""

    def __init__(self, name: str, validator: dict[str, Any] | None = None) -> None:
        self.name = name
        self.validator = copy.deepcopy(validator) if validator else None
        self._documents: list[dict[str, Any]] = []
        self._indexes: dict[str, _IndexSpec] = {
            "_id_": _IndexSpec(name="_id_", keys=[("_id", ASCENDING)], unique=True)
        }

    def _check_unique_constraints(
        self,
        candidate: dict[str, Any],
        *,
        exclude_index: int | None = None,
    ) -> None:
        for spec in self._indexes.values():
            if not spec.unique:
                continue
            cand_tuple = tuple(_get_nested_value(candidate, k) for k, _ in spec.keys)
            if all(val is _MISSING for val in cand_tuple):
                continue
            for idx, existing in enumerate(self._documents):
                if exclude_index is not None and idx == exclude_index:
                    continue
                exist_tuple = tuple(_get_nested_value(existing, k) for k, _ in spec.keys)
                if cand_tuple == exist_tuple:
                    raise DuplicateKeyError(
                        f"Violação de índice único '{spec.name}' na coleção '{self.name}'."
                    )

    def create_index(
        self,
        keys: str | list[tuple[str, int]],
        *,
        unique: bool = False,
        name: str | None = None,
        **_kwargs,
    ) -> str:
        normalized = [(keys, ASCENDING)] if isinstance(keys, str) else list(keys)
        index_name = name or "_".join(f"{k}_{d}" for k, d in normalized)
        spec = _IndexSpec(name=index_name, keys=normalized, unique=unique)

        if unique:
            seen: set[tuple[Any, ...]] = set()
            for doc in self._documents:
                key_tuple = tuple(_get_nested_value(doc, k) for k, _ in normalized)
                if all(v is _MISSING for v in key_tuple):
                    continue
                if key_tuple in seen:
                    raise DuplicateKeyError(
                        f"Documentos duplicados impedem criação do índice único '{index_name}'."
                    )
                seen.add(key_tuple)

        self._indexes[index_name] = spec
        return index_name

    def insert_one(self, document: dict[str, Any]) -> InsertOneResult:
        doc = copy.deepcopy(document)
        if "_id" not in doc:
            doc["_id"] = ObjectId()
        self._check_unique_constraints(doc)
        self._documents.append(doc)
        return InsertOneResult(doc["_id"], acknowledged=True)

    def insert_many(self, documents: Iterable[dict[str, Any]]) -> InsertManyResult:
        inserted_ids = []
        for document in documents:
            res = self.insert_one(document)
            inserted_ids.append(res.inserted_id)
        return InsertManyResult(inserted_ids, acknowledged=True)

    def find_one(
        self,
        filter: dict[str, Any] | None = None,
        projection: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        for doc in self._documents:
            if _matches_filter(doc, filter):
                return _apply_projection(doc, projection)
        return None

    def find(
        self,
        filter: dict[str, Any] | None = None,
        projection: dict[str, int] | None = None,
    ) -> InMemoryMongoCursor:
        matched = [
            _apply_projection(doc, projection)
            for doc in self._documents
            if _matches_filter(doc, filter)
        ]
        return InMemoryMongoCursor(matched)

    def count_documents(self, filter: dict[str, Any] | None = None) -> int:
        return sum(1 for doc in self._documents if _matches_filter(doc, filter))

    def _apply_update(self, doc: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        updated = copy.deepcopy(doc)
        for op, fields in update.items():
            if op == "$set":
                for k, v in fields.items():
                    _set_nested_value(updated, k, v)
            elif op == "$unset":
                for k in fields:
                    _unset_nested_value(updated, k)
            elif op == "$inc":
                for k, delta in fields.items():
                    cur = _get_nested_value(updated, k)
                    base = 0 if cur is _MISSING else cur
                    _set_nested_value(updated, k, base + delta)
            elif op == "$push":
                for k, val in fields.items():
                    cur = _get_nested_value(updated, k)
                    arr = [] if cur is _MISSING else list(cur)
                    arr.append(copy.deepcopy(val))
                    _set_nested_value(updated, k, arr)
            elif op == "$pull":
                for k, val in fields.items():
                    cur = _get_nested_value(updated, k)
                    if isinstance(cur, list):
                        _set_nested_value(updated, k, [item for item in cur if item != val])
            else:
                raise ValueError(f"Operador de atualização não suportado no mock: {op}")
        return updated

    def update_one(
        self,
        filter: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
    ) -> UpdateResult:
        for idx, doc in enumerate(self._documents):
            if _matches_filter(doc, filter):
                candidate = self._apply_update(doc, update)
                self._check_unique_constraints(candidate, exclude_index=idx)
                modified = 1 if candidate != doc else 0
                self._documents[idx] = candidate
                return UpdateResult(
                    {"n": 1, "nModified": modified, "ok": 1.0, "updatedExisting": True},
                    acknowledged=True,
                )

        if upsert:
            base_doc = {k: v for k, v in filter.items() if not k.startswith("$") and not isinstance(v, dict)}
            new_doc = self._apply_update(base_doc, update)
            res = self.insert_one(new_doc)
            return UpdateResult(
                {"n": 1, "nModified": 0, "upserted": res.inserted_id, "ok": 1.0, "updatedExisting": False},
                acknowledged=True,
            )

        return UpdateResult({"n": 0, "nModified": 0, "ok": 1.0, "updatedExisting": False}, acknowledged=True)

    def update_many(self, filter: dict[str, Any], update: dict[str, Any]) -> UpdateResult:
        matched = 0
        modified = 0
        for idx, doc in enumerate(self._documents):
            if _matches_filter(doc, filter):
                matched += 1
                candidate = self._apply_update(doc, update)
                self._check_unique_constraints(candidate, exclude_index=idx)
                if candidate != doc:
                    modified += 1
                self._documents[idx] = candidate
        return UpdateResult(
            {"n": matched, "nModified": modified, "ok": 1.0, "updatedExisting": matched > 0},
            acknowledged=True,
        )

    def delete_one(self, filter: dict[str, Any]) -> DeleteResult:
        for idx, doc in enumerate(self._documents):
            if _matches_filter(doc, filter):
                self._documents.pop(idx)
                return DeleteResult({"n": 1, "ok": 1.0}, acknowledged=True)
        return DeleteResult({"n": 0, "ok": 1.0}, acknowledged=True)

    def delete_many(self, filter: dict[str, Any]) -> DeleteResult:
        remaining = [doc for doc in self._documents if not _matches_filter(doc, filter)]
        deleted = len(self._documents) - len(remaining)
        self._documents = remaining
        return DeleteResult({"n": deleted, "ok": 1.0}, acknowledged=True)

    def drop(self) -> None:
        self._documents.clear()


class InMemoryMongoDatabase:
    """Banco de dados MongoDB simulado em memória."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._collections: dict[str, InMemoryMongoCollection] = {}

    def __getitem__(self, name: str) -> InMemoryMongoCollection:
        return self.get_collection(name)

    def get_collection(self, name: str) -> InMemoryMongoCollection:
        if name not in self._collections:
            self._collections[name] = InMemoryMongoCollection(name)
        return self._collections[name]

    def create_collection(
        self,
        name: str,
        *,
        validator: dict[str, Any] | None = None,
        **_kwargs,
    ) -> InMemoryMongoCollection:
        if name not in self._collections:
            self._collections[name] = InMemoryMongoCollection(name, validator=validator)
        elif validator is not None:
            self._collections[name].validator = copy.deepcopy(validator)
        return self._collections[name]

    def list_collection_names(self) -> list[str]:
        return list(self._collections.keys())

    def drop_collection(self, name: str) -> None:
        self._collections.pop(name, None)

    def command(self, command: str | dict[str, Any], *args, **kwargs) -> dict[str, Any]:
        cmd_name = command if isinstance(command, str) else next(iter(command.keys()), "")
        if cmd_name == "ping":
            return {"ok": 1.0}
        if cmd_name == "collMod":
            col_name = args[0] if args else kwargs.get("collMod")
            if isinstance(col_name, str):
                col = self.get_collection(col_name)
                if "validator" in kwargs:
                    col.validator = copy.deepcopy(kwargs["validator"])
            return {"ok": 1.0}
        return {"ok": 1.0}


class InMemoryMongoClient:
    """Cliente MongoDB simulado em memória com suporte a múltiplos bancos."""

    def __init__(self) -> None:
        self._databases: dict[str, InMemoryMongoDatabase] = {
            "admin": InMemoryMongoDatabase("admin")
        }

    def __getitem__(self, name: str) -> InMemoryMongoDatabase:
        return self.get_database(name)

    @property
    def admin(self) -> InMemoryMongoDatabase:
        return self.get_database("admin")

    def get_database(self, name: str) -> InMemoryMongoDatabase:
        if name not in self._databases:
            self._databases[name] = InMemoryMongoDatabase(name)
        return self._databases[name]

    def list_database_names(self) -> list[str]:
        return list(self._databases.keys())

    def close(self) -> None:
        self._databases.clear()
