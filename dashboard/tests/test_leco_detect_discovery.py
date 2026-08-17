"""Detection of infrastructure in non-trivial repositories.

These cover the three failures that made a real monorepo (18 Cloudflare Workers, compose
under ``infra/docker/``) onboard as an app with no infrastructure at all:

1. compose searched only the repo root and ``docker/``;
2. the wrangler walk-up left the repository and adopted a sibling project's config;
3. Worker ports were invented as a sequential 8787+ range instead of read from compose.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

import leco_detect  # noqa: E402


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


COMPOSE_MESH = """
name: edge-mesh
services:
  edge:
    image: node
    ports:
      - '8787:8787'
      - '8788:8788'
      - '8789:8789'
  origin-ssr:
    image: nginx
    ports:
      - '8081:80'
  origin-spa:
    image: nginx
    ports:
      - '8082:80'
"""


class TestComposeDiscovery(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_finds_compose_at_repo_root(self):
        write(self.root / "docker-compose.yml", "services: {}")
        found = leco_detect._list_compose_files(self.root)
        self.assertEqual([p.as_posix() for p in found], ["docker-compose.yml"])

    def test_finds_compose_nested_under_infra_docker(self):
        """The case that reported 'no compose signals' for a real monorepo."""
        write(self.root / "infra/docker/docker-compose.yml", "services: {}")
        found = leco_detect._list_compose_files(self.root)
        self.assertIn("infra/docker/docker-compose.yml", [p.as_posix() for p in found])

    def test_searches_common_infra_directories(self):
        for sub in ("docker", "infra", "deploy", "ops", ".docker"):
            with self.subTest(sub=sub):
                root = self.root / sub.replace(".", "dot")
                write(root / sub / "docker-compose.yml", "services: {}")
                found = [p.as_posix() for p in leco_detect._list_compose_files(root)]
                self.assertIn(f"{sub}/docker-compose.yml", found)

    def test_root_compose_still_ranks_first(self):
        write(self.root / "docker-compose.yml", "services: {}")
        write(self.root / "infra/docker/docker-compose.yml", "services: {}")
        primary = leco_detect._pick_primary_compose(leco_detect._list_compose_files(self.root))
        self.assertEqual(primary.as_posix(), "docker-compose.yml")

    def test_no_compose_returns_empty_not_error(self):
        self.assertEqual(leco_detect._list_compose_files(self.root), [])


class TestPortGrouping(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write(self.root / "infra/docker/docker-compose.yml", COMPOSE_MESH)
        self.rel = Path("infra/docker/docker-compose.yml")

    def tearDown(self):
        self._tmp.cleanup()

    def test_ports_grouped_by_service(self):
        groups = leco_detect._scan_compose_ports_by_service(self.root, self.rel)
        self.assertEqual(groups["edge"], [8787, 8788, 8789])
        self.assertEqual(groups["origin-ssr"], [8081])

    def test_primary_group_is_the_mesh_not_the_fixtures(self):
        """Pairing Workers against every port put an nginx fixture port on a Worker."""
        ports = leco_detect._primary_port_group(self.root, [self.rel])
        self.assertEqual(ports, [8787, 8788, 8789])
        self.assertNotIn(8081, ports)

    def test_single_service_app_uses_its_own_ports(self):
        root = self.root / "single"
        write(root / "docker-compose.yml", "services:\n  web:\n    ports:\n      - '3000:3000'\n")
        ports = leco_detect._primary_port_group(root, [Path("docker-compose.yml")])
        self.assertEqual(ports, [3000])

    def test_missing_compose_returns_empty(self):
        self.assertEqual(leco_detect._primary_port_group(self.root, []), [])
        self.assertEqual(
            leco_detect._scan_compose_ports_by_service(self.root, Path("nope.yml")), {}
        )


class TestRepositoryBoundary(unittest.TestCase):
    """The walk-up must never leave the repository it started in."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self._tmp.name)
        # A workspace parent holding two unrelated checkouts.
        write(self.parent / "other-project/wrangler.toml", 'name = "someone-elses-worker"\n')
        self.app = self.parent / "my-app"
        (self.app / ".git").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_does_not_adopt_a_sibling_projects_config(self):
        rel = leco_detect._detect_wrangler_relpath_from_base(self.app)
        self.assertIsNone(
            rel,
            f"walked out of the repository and picked up {rel!r} from a sibling checkout",
        )

    def test_finds_its_own_config(self):
        write(self.app / "wrangler.toml", 'name = "mine"\n')
        rel = leco_detect._detect_wrangler_relpath_from_base(self.app)
        self.assertEqual(rel, "wrangler.toml")

    def test_finds_config_above_a_subdirectory_inside_the_same_repo(self):
        write(self.app / "wrangler.toml", 'name = "mine"\n')
        sub = self.app / "packages" / "api"
        sub.mkdir(parents=True)
        rel = leco_detect._detect_wrangler_relpath_from_base(sub)
        self.assertIsNotNone(rel)
        self.assertTrue(rel.endswith("wrangler.toml"))
        self.assertTrue(rel.startswith(".."), rel)

    def test_boundary_detected_for_each_vcs_marker(self):
        for marker in (".git", ".hg", ".svn"):
            with self.subTest(marker=marker):
                d = self.parent / f"repo{marker}"
                (d / marker).mkdir(parents=True, exist_ok=True)
                self.assertTrue(leco_detect._is_repo_boundary(d))

    def test_plain_directory_is_not_a_boundary(self):
        plain = self.parent / "plain"
        plain.mkdir()
        self.assertFalse(leco_detect._is_repo_boundary(plain))



class TestMeshRouting(unittest.TestCase):
    """One container publishing many ports gets one route per port."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write(self.root / "infra/docker/docker-compose.yml", COMPOSE_MESH)
        self.localhost = {
            "infrastructure": {
                "dockerCompose": {
                    "composeFile": "infra/docker/docker-compose.yml",
                    "projectName": "edge-mesh",
                },
                "runtimes": [
                    {"id": "core-router", "port": 8787},
                    {"id": "app-www", "port": 8788},
                    {"id": "app-admin", "port": 8789},
                ],
            }
        }
        self.manifest = {"name": "edge", "root": "."}

    def tearDown(self):
        self._tmp.cleanup()

    def entries(self):
        return leco_detect._infer_mesh_routing_entries(
            self.localhost, self.root, self.manifest, "edge"
        )

    def test_one_entry_per_published_port_of_the_mesh_service(self):
        got = self.entries()
        self.assertEqual(len(got), 3)
        self.assertEqual([e["backendPort"] for e in got], [8787, 8788, 8789])

    def test_lowest_port_is_the_front_door(self):
        self.assertEqual(self.entries()[0]["hostname"], "edge.lh")

    def test_other_ports_get_named_subdomains_from_runtime_ids(self):
        hosts = [e["hostname"] for e in self.entries()]
        self.assertIn("app-www.edge.lh", hosts)
        self.assertIn("app-admin.edge.lh", hosts)

    def test_backend_host_is_the_container_not_the_fixture(self):
        for e in self.entries():
            self.assertNotIn("origin", e["backendHost"])

    def test_uses_schema_accepted_shape(self):
        """RoutingEntry rejects a bare ``frontend``; single-target routes need backendHost."""
        for e in self.entries():
            self.assertIn("backendHost", e)
            self.assertIn("backendPort", e)
            self.assertNotIn("frontend", e)

    def test_ports_without_a_runtime_fall_back_to_a_port_label(self):
        self.localhost["infrastructure"]["runtimes"] = []
        hosts = [e["hostname"] for e in self.entries()]
        self.assertIn("port-8788.edge.lh", hosts)

    def test_single_port_service_is_not_treated_as_a_mesh(self):
        root = self.root / "solo"
        write(root / "docker-compose.yml", "services:\n  web:\n    ports:\n      - '3000:3000'\n")
        localhost = {
            "infrastructure": {"dockerCompose": {"composeFile": "docker-compose.yml"}}
        }
        self.assertEqual(
            leco_detect._infer_mesh_routing_entries(localhost, root, self.manifest, "solo"), []
        )

    def test_compose_services_load_without_a_written_manifest(self):
        """Preview runs before leco.app.yaml exists; the loader must still resolve compose."""
        loaded = leco_detect._load_compose_services_for_localhost(
            self.localhost, self.root, self.manifest
        )
        self.assertIsNotNone(loaded, "compose services did not load during preview")
        self.assertIn("edge", loaded[0])

    def test_slug_component_normalizes_to_a_dns_label(self):
        self.assertEqual(leco_detect._slug_component("Mod_Render"), "mod-render")
        self.assertEqual(leco_detect._slug_component("!!!"), "svc")

if __name__ == "__main__":
    unittest.main()
