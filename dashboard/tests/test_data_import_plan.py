"""Tests for hosted-app seed data import plan builder."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_CLI = Path(__file__).resolve().parents[2] / "tools" / "deploy-cli"
if str(_CLI) not in sys.path:
    sys.path.insert(0, str(_CLI))

from leco_app.data_import import plan as _plan_mod  # noqa: E402
from leco_app.data_import.orchestrator import run_import_plan_stream  # noqa: E402

build_import_plan = _plan_mod.build_import_plan
data_dir_for_manifest = _plan_mod.data_dir_for_manifest
entry_id = _plan_mod.entry_id


class TestDataImportPlan(unittest.TestCase):
    def test_no_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mp = Path(td) / "leco.app.yaml"
            mp.write_text("id: test\n", encoding="utf-8")
            plan = build_import_plan(mp)
            self.assertFalse(plan["present"])
            self.assertEqual(plan["items"], [])

    def test_auto_discover_mongodb(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mp = root / "leco.app.yaml"
            mp.write_text("id: botfeed\n", encoding="utf-8")
            data = data_dir_for_manifest(mp)
            bson_dir = data / "mongo" / "clientData"
            bson_dir.mkdir(parents=True)
            (bson_dir / "clients.bson").write_bytes(b"\x00")
            (bson_dir / "clients.metadata.json").write_text("{}", encoding="utf-8")

            plan = build_import_plan(mp, services={"mongo": {"image": "mongo:7"}})
            self.assertTrue(plan["present"])
            self.assertEqual(len(plan["items"]), 1)
            item = plan["items"][0]
            self.assertEqual(item["kind"], "mongodb")
            self.assertEqual(item["database"], "clientData")
            self.assertEqual(item["service"], "mongo")
            self.assertGreater(item["size_bytes"], 0)

    def test_manifest_overrides_auto(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mp = root / "leco.app.yaml"
            mp.write_text("id: app\n", encoding="utf-8")
            data = data_dir_for_manifest(mp)
            data.mkdir()
            (data / "manifest.yaml").write_text(
                "version: 1\nimports:\n"
                "  - id: x\n    kind: redis\n    path: redis/dump.rdb\n",
                encoding="utf-8",
            )
            (data / "redis").mkdir()
            (data / "redis" / "dump.rdb").write_bytes(b"REDIS")

            plan = build_import_plan(mp)
            self.assertEqual(len(plan["items"]), 1)
            self.assertEqual(plan["items"][0]["kind"], "redis")
            self.assertEqual(plan["items"][0]["source"], "manifest")

    def test_missing_manifest_path_warns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mp = root / "leco.app.yaml"
            mp.write_text("id: app\n", encoding="utf-8")
            data = data_dir_for_manifest(mp)
            data.mkdir()
            (data / "manifest.yaml").write_text(
                "version: 1\nimports:\n"
                "  - id: x\n    kind: mysql\n    path: mysql/missing.sql\n    database: appdb\n",
                encoding="utf-8",
            )
            plan = build_import_plan(mp)
            self.assertTrue(any("Missing path" in w for w in plan["warnings"]))


    def test_selected_ids_filters_plan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mp = root / "leco.app.yaml"
            mp.write_text("id: app\n", encoding="utf-8")
            data = data_dir_for_manifest(mp)
            data.mkdir()
            for name in ("alpha", "beta"):
                d = data / "mongo" / name
                d.mkdir(parents=True)
                (d / "x.bson").write_bytes(b"\x00")
            plan = build_import_plan(mp)
            ids = [entry_id(it) for it in plan["items"]]
            self.assertEqual(len(ids), 2)
            events = list(
                run_import_plan_stream(
                    mp,
                    compose_root=root,
                    compose_tail=[],
                    services={},
                    compose_ps=[],
                    dry_run=True,
                    selected_ids=[ids[0]],
                )
            )
            done = [e for e in events if e.get("type") == "done"][0]
            self.assertTrue(done["result"]["ok"])
            logs = "".join(e.get("text", "") for e in events if e.get("type") == "log")
            self.assertIn("1 of 2 step(s)", logs)


if __name__ == "__main__":
    unittest.main()
