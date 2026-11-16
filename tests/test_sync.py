"""Per-PC sync (launcher, MCP servers), shared settings, and panel conversations in the configured project."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import config, devicesync, sidecar


def workspace():
    tmp = Path(tempfile.mkdtemp())
    for d in ("Material", "Graph", "Learner", ".study", "Agents/mcp", "Agents/study-engine"):
        (tmp / d).mkdir(parents=True)
    (tmp / "Agents" / "mcp" / "anki_mcp.py").write_text("# stub\n", encoding="utf-8")
    (tmp / "Agents" / "study-engine" / "study.py").write_text("# stub\n", encoding="utf-8")
    (tmp / "Agents" / "study-engine" / "DEPLOYED.txt").write_text("deployed 2026-11-16 12:00 (abc123)\n", encoding="utf-8")
    plug = tmp / ".obsidian" / "plugins" / "obsidian-local-rest-api"
    plug.mkdir(parents=True)
    (plug / "data.json").write_text(json.dumps({"apiKey": "SECRET-KEY-123", "insecurePort": 27123}), encoding="utf-8")
    home = Path(tempfile.mkdtemp())
    return config.Config(magistrale=tmp), home


class DeviceSync(unittest.TestCase):
    def test_fresh_pc_gets_launcher_and_mcp_servers(self):
        cfg, home = workspace()
        r = devicesync.check(cfg, fix=False, home=home)
        bad = {i["item"] for i in r["items"] if not i["ok"]}
        self.assertTrue({"launcher", "anki MCP", "obsidian MCP"} <= bad)

        r = devicesync.check(cfg, fix=True, home=home)
        fixed = {i["item"] for i in r["items"] if i.get("fixed")}
        self.assertEqual(fixed, {"launcher", "anki MCP", "obsidian MCP"})
        sc = json.loads((home / ".gemini/config/plugins/study/sidecars/panel/sidecar.json").read_text(encoding="utf-8"))
        self.assertEqual(sc["args"][0], str(cfg.magistrale / "Agents" / "study-engine" / "study.py"))
        mcp = json.loads((home / ".gemini/config/mcp_config.json").read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(mcp["anki"]["args"], [str(cfg.magistrale / "Agents" / "mcp" / "anki_mcp.py")])
        self.assertEqual(mcp["obsidian"]["headers"]["Authorization"], "Bearer SECRET-KEY-123")
        self.assertNotIn("SECRET-KEY-123", json.dumps(r))                  # never reported

        again = devicesync.check(cfg, fix=True, home=home)                  # idempotent
        self.assertFalse(any(i.get("fixed") for i in again["items"]))
        self.assertTrue(all(i["ok"] for i in again["items"] if i["item"] != "python packages"))

    def test_stale_anki_path_from_another_pc_is_repointed_and_backed_up(self):
        cfg, home = workspace()
        f = home / ".gemini" / "config" / "mcp_config.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"mcpServers": {"anki": {"command": "python", "args": ["X:\\elsewhere\\anki_mcp.py"]},
                                                "obsidian": {"serverUrl": "http://127.0.0.1:27123/mcp/"}}}), encoding="utf-8")
        devicesync.check(cfg, fix=True, home=home)
        mcp = json.loads(f.read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(mcp["anki"]["args"][0], str(cfg.magistrale / "Agents" / "mcp" / "anki_mcp.py"))
        self.assertNotIn("headers", mcp["obsidian"])                         # an existing entry is left alone
        self.assertTrue(f.with_suffix(".json.bak").is_file())

    def test_shared_settings_apply_on_every_pc(self):
        cfg, _ = workspace()
        (cfg.shared_dir / "settings.json").write_text(json.dumps({"agy_project": "proj-1"}), encoding="utf-8")
        with mock.patch.object(config, "_workspace_above", return_value=cfg.magistrale):
            loaded = config.load(Path(tempfile.mkdtemp()) / "none.json")
        self.assertEqual(loaded.agy_project, "proj-1")
        (cfg.shared_dir / "settings.json").write_text(json.dumps({"anki_url": "x"}), encoding="utf-8")
        with mock.patch.object(config, "_workspace_above", return_value=cfg.magistrale):
            with self.assertRaises(ValueError):                             # per-PC keys can't be shared
                config.load(Path(tempfile.mkdtemp()) / "none.json")


class Project(unittest.TestCase):
    def test_new_conversation_goes_to_the_configured_project(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw["env"]
            return subprocess.CompletedProcess(cmd, 0, stdout='{"conversation_id": "c1"}', stderr="")
        with mock.patch.dict(os.environ, {"ANTIGRAVITY_AGENTAPI_EXE": "ls.exe", "ANTIGRAVITY_PROJECT_ID": "panel"}), \
                mock.patch.object(subprocess, "run", side_effect=fake_run):
            sidecar.new_conversation("Topic: **Word embeddings** (graph node `x`).", project_id="magistrale-online",
                                     title=sidecar.title_of("Topic: **Word embeddings** (graph node `x`)."))
        self.assertEqual(seen["env"]["ANTIGRAVITY_PROJECT_ID"], "magistrale-online")
        self.assertEqual(seen["cmd"][:5], ["ls.exe", "agentapi", "new-conversation", "--model", "flash"])
        self.assertIn("Study · Word embeddings", seen["cmd"])

    def test_titles(self):
        self.assertEqual(sidecar.title_of("x\nTopic: the concept cluster **Attention mechanisms** (22 concepts in the "
                                          "study graph; courses: A). Prepare it as one lecture."),
                         "Study · Attention mechanisms")
        self.assertEqual(sidecar.title_of("Topic: the course `Material/01TXLWS - Network Dynamics`."),
                         "Study · Material/01TXLWS - Network Dynamics")


if __name__ == "__main__":
    unittest.main()
