"""Script para criar os bancos no AWS RDS e aplicar o schema sql/01_schema_relacional.sql."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import psycopg2
from psycopg2 import errors, sql

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure import Config, PostgresConnection  # noqa: E402


def ensure_databases(cfg: Config, target_dbs: tuple[str, ...]) -> bool:
    """Garante a existência dos bancos no servidor PostgreSQL. Retorna True se todos existirem."""
    conn = psycopg2.connect(
        host=cfg.postgres_host,
        port=cfg.postgres_port,
        user=cfg.postgres_user,
        password=cfg.postgres_password,
        dbname="postgres",
        connect_timeout=cfg.connect_timeout_seconds,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false;")
            existing = {row[0] for row in cur.fetchall()}

            all_ready = True
            for db_name in target_dbs:
                if db_name in existing:
                    print(f"[OK] Database '{db_name}' já existe.")
                else:
                    try:
                        cur.execute(
                            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
                        )
                        print(f"[CRIADO] Database '{db_name}' criado com sucesso.")
                    except errors.InsufficientPrivilege:
                        all_ready = False
                        print(
                            f"[AVISO] Usuário '{cfg.postgres_user}' não tem permissão CREATEDB "
                            f"para criar o database '{db_name}'."
                        )
            return all_ready
    finally:
        conn.close()


def apply_universidade_schema(cfg: Config, schema_path: Path, target_db: str) -> None:
    """Recria o schema universidade e carrega todos os dados iniciais."""
    raw_sql = schema_path.read_text(encoding="utf-8")
    # Garante execução idempotente mesmo quando o schema ainda não existe no banco.
    safe_sql = re.sub(
        r"^\s*DROP\s+SCHEMA\s+universidade\s+CASCADE\s*;",
        "DROP SCHEMA IF EXISTS universidade CASCADE;",
        raw_sql,
        count=1,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    conn = psycopg2.connect(
        host=cfg.postgres_host,
        port=cfg.postgres_port,
        user=cfg.postgres_user,
        password=cfg.postgres_password,
        dbname=target_db,
        connect_timeout=cfg.connect_timeout_seconds,
    )
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(safe_sql)
                cur.execute(
                    """
                    SELECT setval(
                        'universidade.seq_turma',
                        COALESCE((SELECT MAX(id_turma) FROM universidade.turma), 1),
                        true
                    );
                    SELECT setval(
                        pg_get_serial_sequence('universidade.curso', 'idcurso'),
                        COALESCE((SELECT MAX(idCurso) FROM universidade.curso), 1),
                        true
                    );
                    SELECT setval(
                        pg_get_serial_sequence('universidade.vinculo', 'idvinculo'),
                        COALESCE((SELECT MAX(idVinculo) FROM universidade.vinculo), 1),
                        true
                    );
                    """
                )
                cur.execute(
                    """
                    DO $$
                    BEGIN
                        REVOKE ALL ON SCHEMA universidade FROM PUBLIC;
                        REVOKE ALL ON ALL TABLES IN SCHEMA universidade FROM PUBLIC;
                        REVOKE ALL ON ALL SEQUENCES IN SCHEMA universidade FROM PUBLIC;
                        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'professor') THEN
                            GRANT USAGE ON SCHEMA universidade TO professor;
                            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA universidade TO professor;
                            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA universidade TO professor;
                        END IF;
                        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aluno') THEN
                            GRANT USAGE ON SCHEMA universidade TO aluno;
                            GRANT SELECT ON ALL TABLES IN SCHEMA universidade TO aluno;
                        END IF;
                    END
                    $$;
                    """
                )

        with conn.cursor() as cur:
            tables = (
                "usuario",
                "professor",
                "estudante",
                "departamento",
                "curso",
                "vinculo",
                "disciplina",
                "turma",
                "leciona",
                "cursa",
            )
            counts = {}
            for table in tables:
                cur.execute(
                    sql.SQL("SELECT COUNT(*) FROM universidade.{}").format(
                        sql.Identifier(table)
                    )
                )
                counts[table] = cur.fetchone()[0]
        print(f"[OK] Schema 'universidade' atualizado em '{target_db}':")
        for table, total in counts.items():
            print(f"     - universidade.{table}: {total} registros")
    finally:
        conn.close()


def verify_connections(cfg: Config, target_dbs: tuple[str, ...]) -> None:
    """Valida que PostgresConnection conecta na AWS sem cair em fallback."""
    for db_name in target_dbs:
        with PostgresConnection(cfg, database=db_name) as pg:
            print(
                f"[VERIFICAÇÃO] {pg.database} -> fallback={pg.is_local_fallback}, "
                f"ok={pg.test_connection()}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inicializa bancos no RDS e aplica sql/01_schema_relacional.sql."
    )
    parser.add_argument(
        "--db",
        help="Database alvo para aplicar o schema relacional (padrão: POSTGRES_DB_OPERACIONAL).",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=PROJECT_ROOT / "sql" / "01_schema_relacional.sql",
        help="Caminho do script SQL (padrão: sql/01_schema_relacional.sql).",
    )
    args = parser.parse_args()

    cfg = Config.from_env(override=True)
    if not cfg.has_postgres_credentials():
        print("[ERRO] Credenciais do PostgreSQL ausentes no .env.")
        return 1

    schema_path = args.schema
    if not schema_path.exists():
        print(f"[ERRO] Arquivo não encontrado: {schema_path}")
        return 1

    target_op = args.db or cfg.postgres_db_operacional
    ensure_databases(cfg, (target_op, cfg.postgres_db_dw))
    apply_universidade_schema(cfg, schema_path, target_op)
    verify_connections(cfg, (target_op, cfg.postgres_db_dw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
