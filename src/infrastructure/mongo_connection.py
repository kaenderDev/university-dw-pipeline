"""Gerenciamento de conexão com MongoDB (AWS) e fallback automático em memória."""

from __future__ import annotations

import logging
from typing import Any

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from src.infrastructure.config import Config
from src.infrastructure.mongo_mock import InMemoryMongoClient

logger = logging.getLogger(__name__)


class MongoConnection:
    """Conexão PyMongo com MongoDB (AWS) e fallback automático para mock em memória."""

    def __init__(
        self,
        config: Config | None = None,
        database: str | None = None,
        *,
        auto_connect: bool = True,
    ) -> None:
        self.config: Config = config if config is not None else Config.from_env()
        self.database_name: str = database or self.config.mongo_db_name
        self.client: MongoClient | InMemoryMongoClient | None = None
        self.database: Any = None
        self.is_local_fallback: bool = False
        self.fallback_reason: str | None = None

        if auto_connect:
            self.connect()

    def connect(self) -> Any:
        """Conecta ao MongoDB na AWS ou ativa o mock em memória."""
        self.close()

        if self.config.use_local_fallback:
            return self._activate_fallback("USE_LOCAL_FALLBACK=True")

        if not self.config.has_mongo_credentials():
            return self._activate_fallback("Credenciais do MongoDB ausentes no .env")

        aws_client: MongoClient | None = None
        try:
            aws_client = MongoClient(
                self.config.mongo_uri,
                serverSelectionTimeoutMS=self.config.connect_timeout_seconds * 1000,
                connectTimeoutMS=self.config.connect_timeout_seconds * 1000,
            )
            aws_client.admin.command("ping")

            self.client = aws_client
            self.database = self.client[self.database_name]
            self.is_local_fallback = False
            self.fallback_reason = None
            logger.info("Conectado ao MongoDB AWS (%s).", self.database_name)
            return self.database
        except (PyMongoError, OSError, ValueError) as exc:
            if aws_client is not None:
                aws_client.close()
            reason = f"Falha de conexão com MongoDB AWS: {exc}"
            logger.warning("%s. Ativando mock em memória local.", reason)
            return self._activate_fallback(reason)

    def _activate_fallback(self, reason: str) -> Any:
        """Inicializa cliente MongoDB em memória preservando a interface do PyMongo."""
        self.client = InMemoryMongoClient()
        self.database = self.client[self.database_name]
        self.is_local_fallback = True
        self.fallback_reason = reason
        logger.info("Modo fallback MongoDB em memória ativo para '%s' (%s).", self.database_name, reason)
        return self.database

    def get_database(self) -> Any:
        """Retorna a instância do banco MongoDB (real ou mock em memória)."""
        if self.database is None:
            return self.connect()
        return self.database

    def get_collection(self, name: str) -> Any:
        """Retorna uma coleção específica do banco ativo."""
        return self.get_database()[name]

    def ping(self) -> bool:
        """Verifica se o cliente MongoDB ativo responde ao comando ping."""
        try:
            if self.client is None:
                self.connect()
            assert self.client is not None
            result = self.client.admin.command("ping")
            return bool(result.get("ok"))
        except (PyMongoError, OSError):
            return False

    def close(self) -> None:
        """Fecha o cliente MongoDB ativo."""
        if self.client is not None:
            self.client.close()
            self.client = None
            self.database = None

    def __enter__(self) -> MongoConnection:
        self.get_database()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
