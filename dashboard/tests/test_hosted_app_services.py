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
    _resolve_mongodb_database,
)
from port_utils import (  # noqa: E402
    host_port_in_use,
    pick_first_free_mongo_host_port,
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
        spec = {"ports": ["27018:27017"], "image": "mongo:7"}
        creds = {"database": "botfeed"}
        mgmt = _management_uis_for_data_store("mongodb", "mongo", spec, creds)
        self.assertEqual(len(mgmt), 1)
        self.assertIn("Compass (host)", mgmt[0]["label"])
        self.assertIn("127.0.0.1:27018", mgmt[0]["url"])
        self.assertNotIn("mongo:27017", mgmt[0]["url"])

    def test_management_ui_omitted_without_publish(self) -> None:
        spec = {"image": "mongo:7"}
        creds = {"database": "botfeed"}
        mgmt = _management_uis_for_data_store("mongodb", "mongo", spec, creds)
        self.assertEqual(mgmt, [])

    def test_mongo_unpublished_no_host_endpoint(self) -> None:
        eps = _build_connection_endpoints(
            "mongodb",
            "mongo",
            {"image": "mongo:7"},
            {},
            ["mongodb://mongo:27017/"],
            [],
        )
        scopes = {e["scope"] for e in eps}
        self.assertIn("docker", scopes)
        self.assertNotIn("host", scopes)

    def test_mongo_published_uses_mapped_host_port(self) -> None:
        eps = _build_connection_endpoints(
            "mongodb",
            "mongo",
            {"ports": ["27018:27017"], "image": "mongo:7"},
            {"database": "clientData"},
            [],
            [],
        )
        host_uri = next(e["uri"] for e in eps if e["scope"] == "host")
        self.assertIn("127.0.0.1:27018", host_uri)
        self.assertIn("/clientData", host_uri)
        self.assertNotIn(":27017", host_uri.split("127.0.0.1")[1])

    def test_resolve_database_from_leco_env(self) -> None:
        services = {
            "mongo": {"image": "mongo:7", "_compose_file": "/tmp/docker-compose.yml"},
            "server": {
                "image": "node:20",
                "_compose_file": "/tmp/docker-compose.yml",
                "environment": {
                    "LECO_MONGO_URI": "mongodb://mongo:27017/",
                    "LECO_MONGO_DATABASE": "clientData",
                },
            },
        }
        db = _resolve_mongodb_database(
            {},
            ["mongodb://mongo:27017/"],
            services=services,
            mongo_service_name="mongo",
        )
        self.assertEqual(db, "clientData")

    def test_enrich_exposes_host_and_docker_endpoints(self) -> None:
        services = {
            "mongo": {
                "image": "mongo:7",
                "ports": ["27018:27017"],
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
        self.assertIn("127.0.0.1:27018", host_uri)
        self.assertIn("mongo", docker_uri)
        self.assertEqual(items[0].get("host_access"), "published")

    def test_enrich_unpublished_mongo_host_access_note(self) -> None:
        services = {
            "mongo": {
                "image": "mongo:7",
                "_compose_file": "/tmp/docker-compose.yml",
            },
            "server": {
                "image": "node:20",
                "_compose_file": "/tmp/docker-compose.yml",
                "environment": {"LECO_MONGO_URI": "mongodb://mongo:27017/"},
            },
        }
        items = [
            {
                "name": "mongo",
                "kind": "mongodb",
                "credentials": {},
                "connection_strings": [],
                "management_uis": [],
                "notes": "",
            }
        ]
        hints = {"mongodb": ["mongodb://mongo:27017/"]}
        _enrich_data_store_items(items, services, hints)
        self.assertEqual(items[0].get("host_access"), "not_published")
        self.assertNotIn("host", {e["scope"] for e in items[0]["connection_endpoints"]})
        self.assertIn("Not published", items[0].get("notes", ""))

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

    def test_redis_unpublished_no_host_endpoint(self) -> None:
        eps = _build_connection_endpoints(
            "redis",
            "redis",
            {"image": "redis:7"},
            {},
            [],
            [],
        )
        scopes = {e["scope"] for e in eps}
        self.assertIn("docker", scopes)
        self.assertNotIn("host", scopes)
        self.assertIn("host_lh", scopes)


class TestMongoHostPortPicker(unittest.TestCase):
    def test_pick_skips_27017(self) -> None:
        port = pick_first_free_mongo_host_port(skip_ports=(27017,))
        self.assertIsNotNone(port)
        self.assertNotEqual(port, 27017)

    def test_pick_returns_none_when_all_busy(self) -> None:
        import unittest.mock as mock

        with mock.patch("port_utils.host_port_in_use", return_value=True):
            self.assertIsNone(pick_first_free_mongo_host_port())

    def test_host_port_in_use_detects_bound_port(self) -> None:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            self.assertTrue(host_port_in_use(port))
        finally:
            s.close()


class TestComposeServiceMerge(unittest.TestCase):
    def test_overlay_preserves_base_ports(self) -> None:
        from hosted_app_services import _merge_compose_service_specs

        base = {
            "image": "mongo:7",
            "ports": ["27018:27017"],
            "networks": ["botfeed-network"],
        }
        overlay = {"networks": {"lh-network": {"external": True}}}
        merged = _merge_compose_service_specs(base, overlay, compose_file="/tmp/overlay.yml")
        self.assertEqual(merged.get("ports"), ["27018:27017"])
        self.assertIn("botfeed-network", merged.get("networks", {}))
        self.assertIn("lh-network", merged.get("networks", {}))


class TestEnvDictFromSpec(unittest.TestCase):
    def test_list_environment(self) -> None:
        spec = {"environment": ["FOO=bar", "BAZ=qux"]}
        env = _env_dict_from_spec(spec, Path("/tmp"))
        self.assertEqual(env.get("FOO"), "bar")
        self.assertEqual(env.get("BAZ"), "qux")


if __name__ == "__main__":
    unittest.main()
