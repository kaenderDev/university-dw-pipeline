"""Gerenciamento de conexão com PostgreSQL (AWS RDS) e fallback automático para SQLite."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.infrastructure.config import Config

logger = logging.getLogger(__name__)


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """Garante integridade referencial nativa também no SQLite em modo fallback."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


class PostgresConnection:
    """Conexão SQLAlchemy com PostgreSQL (AWS RDS) e fallback local para SQLite."""

    def __init__(
        self,
        config: Config | None = None,
        database: str | None = None,
        *,
        auto_connect: bool = True,
    ) -> None:
        self.config: Config = config if config is not None else Config.from_env()
        self.database: str = database or self.config.postgres_db_operacional
        self.engine: Engine | None = None
        self.session_factory: sessionmaker[Session] | None = None
        self.is_local_fallback: bool = False
        self.fallback_reason: str | None = None

        if auto_connect:
            self.connect()

    @classmethod
    def for_operacional(cls, config: Config | None = None) -> PostgresConnection:
        """Cria conexão para o banco operacional (Parte 1)."""
        cfg = config if config is not None else Config.from_env()
        return cls(cfg, database=cfg.postgres_db_operacional)

    @classmethod
    def for_dw(cls, config: Config | None = None) -> PostgresConnection:
        """Cria conexão para o Data Warehouse em Esquema Estrela (Parte 3)."""
        cfg = config if config is not None else Config.from_env()
        return cls(cfg, database=cfg.postgres_db_dw)

    def connect(self) -> Engine:
        """Conecta ao PostgreSQL na AWS ou ativa fallback local para SQLite."""
        self.close()

        if self.config.use_local_fallback:
            return self._activate_fallback("USE_LOCAL_FALLBACK=True")

        if not self.config.has_postgres_credentials(self.database):
            return self._activate_fallback("Credenciais do PostgreSQL ausentes no .env")

        aws_engine: Engine | None = None
        try:
            aws_engine = create_engine(
                self.config.postgres_url(self.database),
                pool_pre_ping=True,
                connect_args={"connect_timeout": self.config.connect_timeout_seconds},
            )
            with aws_engine.connect() as conn:
                conn.execute(text("SELECT 1"))

            self.engine = aws_engine
            self.session_factory = sessionmaker(
                bind=self.engine,
                autoflush=False,
                autocommit=False,
                expire_on_commit=False,
            )
            self.is_local_fallback = False
            self.fallback_reason = None
            logger.info("Conectado ao PostgreSQL AWS (%s).", self.database)
            return self.engine
        except (SQLAlchemyError, OSError) as exc:
            if aws_engine is not None:
                aws_engine.dispose()
            reason = f"Falha de conexão com PostgreSQL AWS: {exc}"
            logger.warning("%s. Ativando fallback SQLite local.", reason)
            return self._activate_fallback(reason)

    def _activate_fallback(self, reason: str) -> Engine:
        """Inicializa engine SQLite local preservando compatibilidade com SQLAlchemy."""
        sqlite_url = self.config.sqlite_url(self.database)
        engine_kwargs: dict[str, object] = {
            "connect_args": {"check_same_thread": False},
        }
        if self.config.sqlite_in_memory:
            # StaticPool mantém o mesmo banco SQLite em memória entre múltiplas sessões.
            engine_kwargs["poolclass"] = StaticPool

        sqlite_engine = create_engine(sqlite_url, **engine_kwargs)
        event.listen(sqlite_engine, "connect", _enable_sqlite_foreign_keys)

        self.engine = sqlite_engine
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        self.is_local_fallback = True
        self.fallback_reason = reason
        logger.info("Modo fallback SQLite ativo para '%s' (%s).", self.database, reason)
        return self.engine

    def get_engine(self) -> Engine:
        """Retorna o Engine ativo, conectando caso ainda não tenha sido inicializado."""
        if self.engine is None:
            return self.connect()
        return self.engine

    @contextmanager
    def get_session(self) -> Iterator[Session]:
        """Fornece uma sessão transacional com commit/rollback automático."""
        if self.session_factory is None:
            self.connect()
        assert self.session_factory is not None
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def test_connection(self) -> bool:
        """Executa SELECT 1 para verificar se a engine ativa responde corretamente."""
        try:
            engine = self.get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except (SQLAlchemyError, OSError):
            return False

    def close(self) -> None:
        """Libera o pool de conexões da engine atual."""
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
            self.session_factory = None

    def __enter__(self) -> PostgresConnection:
        self.get_engine()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
