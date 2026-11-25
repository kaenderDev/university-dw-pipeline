"""Configurações da aplicação e leitura de variáveis de ambiente (.env)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from sqlalchemy.engine import URL

logger = logging.getLogger(__name__)

_TRUTHY_VALUES = {"1", "true", "t", "yes", "y", "sim", "s", "on"}
_FALSY_VALUES = {"0", "false", "f", "no", "n", "nao", "não", "off", ""}


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    """Converte strings e booleanos de variáveis de ambiente para bool."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in _TRUTHY_VALUES:
        return True
    if normalized in _FALSY_VALUES:
        return False
    logger.warning("Valor booleano inválido '%s'; usando padrão %s.", value, default)
    return default


def _parse_int(value: str | int | None, default: int) -> int:
    """Converte valores inteiros de variáveis de ambiente com fallback seguro."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Valor inteiro inválido '%s'; usando padrão %s.", value, default)
        return default


def _mask_mongo_uri(uri: str) -> str:
    """Mascara usuário/senha em uma URI do MongoDB para exibição segura em logs."""
    if not uri:
        return ""
    try:
        parts = urlsplit(uri)
        if parts.password is None:
            return uri
        user = parts.username or ""
        host_part = parts.hostname or ""
        if parts.port:
            host_part = f"{host_part}:{parts.port}"
        netloc = f"{user}:***@{host_part}" if user else f"***@{host_part}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except ValueError:
        return "***"


@dataclass
class Config:
    """Configurações centralizadas para PostgreSQL (AWS RDS), MongoDB e fallback local."""

    postgres_host: str = ""
    postgres_port: int = 5432
    postgres_user: str = ""
    postgres_password: str = ""
    postgres_db_operacional: str = "universidade"
    postgres_db_dw: str = "universidade_dw"
    mongo_uri: str = ""
    mongo_db_name: str = "universidade_nosql"
    use_local_fallback: bool = False
    connect_timeout_seconds: int = 5
    local_sqlite_dir: Path | None = None
    sqlite_in_memory: bool = False

    @classmethod
    def from_env(
        cls,
        env_path: str | Path | None = None,
        *,
        override: bool = False,
        sqlite_in_memory: bool = False,
        local_sqlite_dir: str | Path | None = None,
    ) -> Config:
        """Carrega o arquivo .env (se existir) e constrói uma instância de Config."""
        if env_path is not None:
            load_dotenv(dotenv_path=Path(env_path), override=override)
        else:
            load_dotenv(override=override)

        sqlite_dir = Path(local_sqlite_dir) if local_sqlite_dir is not None else Path.cwd()

        return cls(
            postgres_host=os.getenv("POSTGRES_HOST", "").strip(),
            postgres_port=_parse_int(os.getenv("POSTGRES_PORT"), 5432),
            postgres_user=os.getenv("POSTGRES_USER", "").strip(),
            postgres_password=os.getenv("POSTGRES_PASSWORD", ""),
            postgres_db_operacional=os.getenv("POSTGRES_DB_OPERACIONAL", "universidade").strip()
            or "universidade",
            postgres_db_dw=os.getenv("POSTGRES_DB_DW", "universidade_dw").strip()
            or "universidade_dw",
            mongo_uri=os.getenv("MONGO_URI", "").strip(),
            mongo_db_name=os.getenv("MONGO_DB_NAME", "universidade_nosql").strip()
            or "universidade_nosql",
            use_local_fallback=parse_bool(os.getenv("USE_LOCAL_FALLBACK"), default=False),
            connect_timeout_seconds=_parse_int(os.getenv("DB_CONNECT_TIMEOUT"), 5),
            local_sqlite_dir=sqlite_dir,
            sqlite_in_memory=sqlite_in_memory,
        )

    def has_postgres_credentials(self, database: str | None = None) -> bool:
        """Verifica se os parâmetros mínimos do PostgreSQL estão preenchidos."""
        target_db = database or self.postgres_db_operacional
        return bool(
            self.postgres_host
            and self.postgres_user
            and self.postgres_password
            and target_db
        )

    def has_mongo_credentials(self) -> bool:
        """Verifica se URI e nome do banco MongoDB estão preenchidos."""
        return bool(self.mongo_uri and self.mongo_db_name)

    def postgres_url(self, database: str | None = None) -> URL:
        """Monta a URL SQLAlchemy para PostgreSQL escapando credenciais com segurança."""
        target_db = database or self.postgres_db_operacional
        return URL.create(
            drivername="postgresql+psycopg2",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=target_db,
        )

    def sqlite_url(self, database: str | None = None) -> URL | str:
        """Monta a URL SQLite para o modo de fallback local."""
        if self.sqlite_in_memory:
            return "sqlite:///:memory:"
        target_db = database or self.postgres_db_operacional
        base_dir = self.local_sqlite_dir or Path.cwd()
        db_path = (base_dir / f"local_{target_db}.sqlite3").resolve()
        return URL.create(drivername="sqlite", database=str(db_path))

    def safe_summary(self) -> dict[str, object]:
        """Retorna resumo das configurações sem expor senhas."""
        return {
            "postgres_host": self.postgres_host,
            "postgres_port": self.postgres_port,
            "postgres_user": self.postgres_user,
            "postgres_password": "***" if self.postgres_password else "",
            "postgres_db_operacional": self.postgres_db_operacional,
            "postgres_db_dw": self.postgres_db_dw,
            "mongo_uri": _mask_mongo_uri(self.mongo_uri),
            "mongo_db_name": self.mongo_db_name,
            "use_local_fallback": self.use_local_fallback,
            "connect_timeout_seconds": self.connect_timeout_seconds,
        }
