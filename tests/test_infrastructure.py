"""Testes unitários para Config, PostgresConnection e MongoConnection."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from src.infrastructure import (
    Config,
    InMemoryMongoClient,
    MongoConnection,
    PostgresConnection,
    parse_bool,
)


class TestConfig(unittest.TestCase):
    def test_parse_bool_variants(self) -> None:
        for val in ("True", "true", "1", "yes", "sim", "on", True):
            self.assertTrue(parse_bool(val))
        for val in ("False", "false", "0", "no", "nao", "não", "off", "", False):
            self.assertFalse(parse_bool(val))
        self.assertTrue(parse_bool(None, default=True))
        self.assertFalse(parse_bool("valor_invalido", default=False))

    def test_from_env_reads_dotenv_and_masks_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "POSTGRES_HOST=rds.aws.test",
                        "POSTGRES_PORT=5433",
                        "POSTGRES_USER=admin_user",
                        "POSTGRES_PASSWORD=p@ss:word/123",
                        "POSTGRES_DB_OPERACIONAL=univ_op",
                        "POSTGRES_DB_DW=univ_dw",
                        "MONGO_URI=mongodb+srv://mongo_u:secret_pw@cluster.aws.test/?retryWrites=true",
                        "MONGO_DB_NAME=univ_nosql",
                        "USE_LOCAL_FALLBACK=True",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                cfg = Config.from_env(env_file, override=True, local_sqlite_dir=tmpdir)

            self.assertEqual(cfg.postgres_host, "rds.aws.test")
            self.assertEqual(cfg.postgres_port, 5433)
            self.assertEqual(cfg.postgres_user, "admin_user")
            self.assertEqual(cfg.postgres_password, "p@ss:word/123")
            self.assertEqual(cfg.postgres_db_operacional, "univ_op")
            self.assertEqual(cfg.postgres_db_dw, "univ_dw")
            self.assertEqual(cfg.mongo_db_name, "univ_nosql")
            self.assertTrue(cfg.use_local_fallback)

            pg_url = cfg.postgres_url()
            self.assertEqual(pg_url.host, "rds.aws.test")
            self.assertEqual(pg_url.password, "p@ss:word/123")
            self.assertEqual(pg_url.database, "univ_op")
            self.assertEqual(cfg.postgres_url(cfg.postgres_db_dw).database, "univ_dw")

            summary = cfg.safe_summary()
            self.assertEqual(summary["postgres_password"], "***")
            self.assertNotIn("secret_pw", str(summary["mongo_uri"]))
            self.assertIn("mongo_u:***@cluster.aws.test", str(summary["mongo_uri"]))


class TestPostgresConnection(unittest.TestCase):
    def test_explicit_local_fallback_uses_sqlite_and_supports_transactions(self) -> None:
        cfg = Config(use_local_fallback=True, sqlite_in_memory=True)
        with PostgresConnection(cfg) as pg:
            self.assertTrue(pg.is_local_fallback)
            self.assertEqual(pg.fallback_reason, "USE_LOCAL_FALLBACK=True")
            self.assertTrue(pg.test_connection())

            with pg.get_session() as session:
                session.execute(text("CREATE TABLE usuario (id INTEGER PRIMARY KEY, nome TEXT NOT NULL)"))
                session.execute(text("INSERT INTO usuario (id, nome) VALUES (1, 'Maria')"))

            with pg.get_session() as session:
                row = session.execute(text("SELECT nome FROM usuario WHERE id = 1")).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "Maria")

            with self.assertRaises(RuntimeError):
                with pg.get_session() as session:
                    session.execute(text("INSERT INTO usuario (id, nome) VALUES (2, 'Rollback')"))
                    raise RuntimeError("forçar rollback")

            with pg.get_session() as session:
                count = session.execute(text("SELECT COUNT(*) FROM usuario")).scalar_one()
                self.assertEqual(count, 1)

    def test_network_failure_activates_sqlite_fallback_automatically(self) -> None:
        cfg = Config(
            postgres_host="rds-inexistente.aws.local",
            postgres_port=5432,
            postgres_user="postgres",
            postgres_password="123",
            postgres_db_operacional="universidade",
            use_local_fallback=False,
            sqlite_in_memory=True,
            connect_timeout_seconds=1,
        )

        real_create_engine = __import__("sqlalchemy").create_engine

        def fake_create_engine(url, *args, **kwargs):
            if str(url).startswith("postgresql"):
                failing_engine = MagicMock()
                failing_engine.connect.side_effect = OperationalError(
                    "SELECT 1", {}, Exception("could not connect to server")
                )
                return failing_engine
            return real_create_engine(url, *args, **kwargs)

        with patch("src.infrastructure.postgres_connection.create_engine", side_effect=fake_create_engine):
            with PostgresConnection(cfg) as pg:
                self.assertTrue(pg.is_local_fallback)
                self.assertIsNotNone(pg.fallback_reason)
                self.assertIn("Falha de conexão", pg.fallback_reason or "")
                self.assertTrue(pg.test_connection())

    def test_connects_to_aws_when_credentials_and_network_are_valid(self) -> None:
        cfg = Config(
            postgres_host="rds.aws.valid",
            postgres_port=5432,
            postgres_user="postgres",
            postgres_password="valid",
            postgres_db_operacional="universidade",
            postgres_db_dw="universidade_dw",
            use_local_fallback=False,
        )

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        with patch("src.infrastructure.postgres_connection.create_engine", return_value=mock_engine) as create_mock:
            pg = PostgresConnection.for_dw(cfg)
            self.assertFalse(pg.is_local_fallback)
            self.assertIsNone(pg.fallback_reason)
            self.assertEqual(pg.database, "universidade_dw")
            self.assertTrue(pg.test_connection())
            called_url = create_mock.call_args.args[0]
            self.assertEqual(called_url.database, "universidade_dw")
            pg.close()


class TestMongoConnection(unittest.TestCase):
    def test_explicit_local_fallback_uses_in_memory_mock_crud_and_unique_index(self) -> None:
        cfg = Config(use_local_fallback=True, mongo_db_name="universidade_nosql")
        with MongoConnection(cfg) as mongo:
            self.assertTrue(mongo.is_local_fallback)
            self.assertIsInstance(mongo.client, InMemoryMongoClient)
            self.assertTrue(mongo.ping())

            db = mongo.get_database()
            db.create_collection("usuarios", validator={"$jsonSchema": {"required": ["cpf"]}})
            usuarios = mongo.get_collection("usuarios")
            usuarios.create_index([("cpf", 1)], unique=True)

            usuarios.insert_one({"cpf": "11122233344", "nome": "Ana", "idade": 22})
            usuarios.insert_one({"cpf": "55566677788", "nome": "Bruno", "idade": 25})

            with self.assertRaises(DuplicateKeyError):
                usuarios.insert_one({"cpf": "11122233344", "nome": "Duplicada"})

            found = list(usuarios.find({"idade": {"$gte": 22}}).sort("idade", -1))
            self.assertEqual([d["nome"] for d in found], ["Bruno", "Ana"])

            usuarios.update_one({"cpf": "11122233344"}, {"$set": {"nome": "Ana Silva"}, "$inc": {"idade": 1}})
            updated = usuarios.find_one({"cpf": "11122233344"})
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["nome"], "Ana Silva")
            self.assertEqual(updated["idade"], 23)

            del_res = usuarios.delete_one({"cpf": "55566677788"})
            self.assertEqual(del_res.deleted_count, 1)
            self.assertEqual(usuarios.count_documents(), 1)

    def test_network_failure_activates_mongo_fallback_automatically(self) -> None:
        cfg = Config(
            mongo_uri="mongodb+srv://user:pass@cluster-inexistente.mongodb.net/",
            mongo_db_name="universidade_nosql",
            use_local_fallback=False,
            connect_timeout_seconds=1,
        )

        failing_client = MagicMock()
        failing_client.admin.command.side_effect = ServerSelectionTimeoutError("timeout")

        with patch("src.infrastructure.mongo_connection.MongoClient", return_value=failing_client):
            with MongoConnection(cfg) as mongo:
                self.assertTrue(mongo.is_local_fallback)
                self.assertIsInstance(mongo.client, InMemoryMongoClient)
                self.assertIn("Falha de conexão", mongo.fallback_reason or "")
                self.assertTrue(mongo.ping())

    def test_connects_to_aws_mongo_when_credentials_and_network_are_valid(self) -> None:
        cfg = Config(
            mongo_uri="mongodb+srv://user:pass@cluster.aws.mongodb.net/",
            mongo_db_name="universidade_nosql",
            use_local_fallback=False,
        )

        mock_client = MagicMock()
        mock_client.admin.command.return_value = {"ok": 1.0}

        with patch("src.infrastructure.mongo_connection.MongoClient", return_value=mock_client):
            with MongoConnection(cfg) as mongo:
                self.assertFalse(mongo.is_local_fallback)
                self.assertIsNone(mongo.fallback_reason)
                self.assertIs(mongo.client, mock_client)
                self.assertTrue(mongo.ping())


if __name__ == "__main__":
    unittest.main()
