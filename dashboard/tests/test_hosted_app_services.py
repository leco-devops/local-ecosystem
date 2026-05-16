"""Tests for hosted_app_services classification and credential extraction."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_DASH = Path(__file__).resolve().parents[1]
if str(_DASH) not in sys.path:
    sys.path.insert(0, str(_DASH))

from hosted_app_services import (  # noqa: E402
    classify_compose_service,
    _build_connection_endpoints,
    _build_host_mongodb_uri,
    _data_uri_for_host,
    _enrich_data_store_items,
    _env_dict_from_spec,
    _extract_credentials,
    _host_port_from_publish,
    _management_uis_for_data_store,
)


class TestClassifyComposeService(unittest.TestCase):
    def test_mysql_image(self) -> None:
        kind = classify_compose_service("db", {"image": "mysql:8"})
        self.assertEqual(kind, "mysql")

    def test_postgres_image(self) -> None:
        kind = classify_compose_service("n8n_postgres", {"image": "postgres:16-alpine"})
        self.assertEqual(kind, "postgres")

    def test_edge_runtime_prefix(self) -> None:
        kind = classify_compose_service(
            "leco-rt-raven-api",
            {"image": "leco/runtime-cloudflare-workers:latest"},
        )
        self.assertEqual(kind, "edge-runtime")

    def test_application_default(self) -> None:
        kind = classify_compose_service("web", {"image": "node:20"})
        self.assertEqual(kind, "application")


class TestExtractCredentials(unittest.TestCase):
    def test_mysql_env(self) -> None:
        env = {
            "MYSQL_DATABASE": "appdb",
            "MYSQL_USER": "app",
            "MYSQL_PASSWORD": "secret",
            "MYSQL_ROOT_PASSWORD": "rootsecret",
        }
        creds = _extract_credentials("mysql", env, "mysql")
        self.assertEqual(creds.get("database"), "appdb")
        self.assertEqual(creds.get("user"), "app")
        self.assertIn("mysql://", creds.get("connection_string", ""))

    def test_postgres_env(self) -> None:
        env = {
            "POSTGRES_DB": "n8n",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "password",
        }
        creds = _extract_credentials("postgres", env, "n8n_postgres")
        self.assertEqual(creds.get("database"), "n8n")
        self.assertIn("postgresql://", creds.get("connection_string", ""))

    def test_redis_no_password(self) -> None:
        env = {"REDIS_HOST": "redis", "REDIS_PORT": "6379"}
        creds = _extract_credentials("redis", env, "redis")
        self.assertEqual(creds.get("connection_string"), "redis://redis:6379")

    def test_mongodb_default_uri(self) -> None:
        creds = _extract_credentials("mongodb", {}, "mongo")
        self.assertIn("mongodb://", creds.get("connection_string", ""))
        self.assertIn("mongo:27017", creds.get("connection_string", ""))

    def test_mongodb_from_app_env_hint(self) -> None:
        services = {
            "mongo": {"image": "mongo:7", "_compose_file": "/tmp/docker-compose.yml"},
            "server": {
                "image": "node:20",
                "_compose_file": "/tmp/docker-compose.yml",
                "environment": {"MONGODB_URI": "mongodb://mongo:27017/botfeed"},
            },
        }
        hints = __import__("hosted_app_services", fromlist=["_collect_connection_hints_from_compose"])._collect_connection_hints_from_compose(services)
        self.assertIn("mongodb://mongo:27017/botfeed", hints["mongodb"])


class TestHostMongoAccess(unittest.TestCase):
    def test_host_port_three_part_mapping(self) -> None:
        spec = {"ports": ["127.0.0.1:27017:27017"]}
        self.assertEqual(_host_port_from_publish(spec), "27017")

    def test_compass_uri_uses_loopback(self) -> None:
        creds = {"user": "root", "password": "s3cret", "database": "botfeed"}
        uri = _build_host_mongodb_uri(creds, "27017")
        self.assertIn("127.0.0.1:27017", uri)
        self.assertIn("root", uri)
        self.assertNotIn("mongo:", uri)

    def test_rewrite_docker_hint_to_host(self) -> None:
        host = _data_uri_for_host("mongodb://mongo:27017/botfeed", "27017")
        self.assertIn("127.0.0.1:27017", host)
        self.assertIn("/botfeed", host)

    def test_management_ui_not_docker_dns(self) -> None:
        spec = {"ports": ["27017:27017"], "image": "mongo:7"}
        creds = {"database": "botfeed"}
        mgmt = _management_uis_for_data_store("mongodb", "mongo", spec, creds)
        self.assertEqual(len(mgmt), 1)
        self.assertIn("Compass (host)", mgmt[0]["label"])
        self.assertIn("127.0.0.1", mgmt[0]["url"])
        self.assertNotIn("mongo:27017", mgmt[0]["url"])

    def test_enrich_exposes_host_and_docker_endpoints(self) -> None:
        services = {
            "mongo": {
                "image": "mongo:7",
                "ports": ["27017:27017"],
                "_compose_file": "/tmp/docker-compose.yml",
                "environment": {
                    "MONGO_INITDB_ROOT_USERNAME": "root",
                    "MONGO_INITDB_ROOT_PASSWORD": "pass",
                    "MONGO_INITDB_DATABASE": "botfeed",
                },
            },
            "server": {
                "image": "node:20",
                "_compose_file": "/tmp/docker-compose.yml",
                "environment": {"MONGODB_URI": "mongodb://mongo:27017/botfeed"},
            },
        }
        items = [
            {
                "name": "mongo",
                "kind": "mongodb",
                "credentials": {"user": "root", "password": "pass", "database": "botfeed"},
                "connection_strings": ["mongodb://root:pass@mongo:27017/botfeed?authSource=admin"],
                "management_uis": [],
                "notes": "",
            }
        ]
        hints = {"mongodb": ["mongodb://mongo:27017/botfeed"]}
        _enrich_data_store_items(items, services, hints)
        eps = items[0]["connection_endpoints"]
        scopes = {e["scope"] for e in eps}
        self.assertIn("host", scopes)
        self.assertIn("docker", scopes)
        host_uri = next(e["uri"] for e in eps if e["scope"] == "host")
        docker_uri = next(e["uri"] for e in eps if e["scope"] == "docker")
        self.assertIn("127.0.0.1", host_uri)
        self.assertIn("mongo", docker_uri)

    def test_mysql_redis_postgres_endpoints(self) -> None:
        mysql_eps = _build_connection_endpoints(
            "mysql",
            "db",
            {"ports": ["3306:3306"]},
            {"user": "app", "password": "pw", "database": "appdb"},
            [],
            [],
        )
        self.assertTrue(any(e["scope"] == "docker" and "db:3306" in e["uri"] for e in mysql_eps))
        self.assertTrue(any(e["scope"] == "host" and "127.0.0.1:3306" in e["uri"] for e in mysql_eps))

        redis_eps = _build_connection_endpoints(
            "redis",
            "redis",
            {"ports": ["6379:6379"]},
            {"password": "secret"},
            [],
            [],
        )
        self.assertTrue(any(e["scope"] == "docker" and "redis:6379" in e["uri"] for e in redis_eps))
        self.assertTrue(any(e["scope"] == "host" and "127.0.0.1:6379" in e["uri"] for e in redis_eps))

        pg_eps = _build_connection_endpoints(
            "postgres",
            "n8n_postgres",
            {"ports": ["5432:5432"]},
            {"user": "postgres", "password": "password", "database": "n8n"},
            [],
            [],
        )
        self.assertTrue(any(e["scope"] == "docker" and "n8n_postgres:5432" in e["uri"] for e in pg_eps))
        self.assertTrue(any(e["scope"] == "host" and "127.0.0.1:5432" in e["uri"] for e in pg_eps))


class TestEnvDictFromSpec(unittest.TestCase):
    def test_list_environment(self) -> None:
        spec = {"environment": ["FOO=bar", "BAZ=qux"]}
        env = _env_dict_from_spec(spec, Path("/tmp"))
        self.assertEqual(env.get("FOO"), "bar")
        self.assertEqual(env.get("BAZ"), "qux")


if __name__ == "__main__":
    unittest.main()
