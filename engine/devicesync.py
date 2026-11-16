"""Keep every PC in step.

What syncs by itself: everything inside the workspace folder (Google Drive) — Material/, Graph/, Learner/,
.study/ (proposals, shared settings), Agents/ (rules, skills, hooks, MCP server, the deployed engine). The panel
reads those files on every request, so data written on one PC shows up on the others as soon as Drive has synced.

What does NOT sync, because it lives on each PC — this module checks it and repairs it:
  launcher     ~/.gemini/config/plugins/study/  must start the deployed engine with THIS PC's Python
  anki MCP     ~/.gemini/config/mcp_config.json "anki" must point at <workspace>/Agents/mcp/anki_mcp.py here
  obsidian MCP ~/.gemini/config/mcp_config.json "obsidian": added if missing (key read from the vault's plugin)
  python deps  numpy, pypdf importable
  engine       a newer deploy from another PC is picked up by restarting the panel's engine (server.py)
  dev clone    a git checkout of this repo is fast-forwarded from GitHub (`study.py sync --pull`)

`check(cfg, fix=True)` runs every time the panel's engine starts; `python study.py sync` runs it by hand.
Secrets are never printed: the Obsidian key is copied from the vault into the MCP config, nothing else.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, sidecar


def _gemini(home=None):
    return Path(home or Path.home()) / ".gemini" / "config"


def deployed_copy(cfg):
    return cfg.magistrale / "Agents" / "study-engine"


def engine_to_run(cfg):
    """The study.py the launcher should start: the synced deployed copy if there is one, else this copy."""
    d = deployed_copy(cfg) / "study.py"
    return d if d.is_file() else config.REPO / "study.py"


def deploy_stamp(cfg):
    f = deployed_copy(cfg) / "DEPLOYED.txt"
    try:
        return f.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


# ── launcher plugin ──

def install_launcher(cfg, study_py=None, home=None):
    """Write the per-PC launcher plugin. Layout mirrors the built-in plugins: plugin.json · assets/logo.svg ·
    sidecars/panel/sidecar.json. Returns the plugin folder."""
    study_py = Path(study_py or engine_to_run(cfg))
    root = _gemini(home) / "plugins" / sidecar.PLUGIN_NAME
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "sidecars" / sidecar.SIDECAR_FOLDER).mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps(sidecar.plugin_manifest(), indent=2), encoding="utf-8")
    (root / "assets" / "logo.svg").write_text(sidecar.LOGO_SVG, encoding="utf-8")
    m = sidecar.manifest(cfg, sys.executable, study_py)
    (root / "sidecars" / sidecar.SIDECAR_FOLDER / "sidecar.json").write_text(
        json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    (root / "README.md").write_text(f"# Study\n\nLauncher for the study engine side panel. Engine: {study_py}\n"
                                    "Kept up to date by the engine itself (`python study.py sync`).\n", encoding="utf-8")
    return root


def _launcher(cfg, fix, home):
    f = _gemini(home) / "plugins" / sidecar.PLUGIN_NAME / "sidecars" / sidecar.SIDECAR_FOLDER / "sidecar.json"
    want = sidecar.manifest(cfg, sys.executable, engine_to_run(cfg))
    try:
        have = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        have = None
    if have == want:
        return {"item": "launcher", "ok": True, "detail": f"starts {want['args'][0]}"}
    why = "missing" if have is None else "points at another engine or Python" \
        if have.get("args") != want["args"] or have.get("command") != want["command"] else "outdated manifest"
    if not fix:
        return {"item": "launcher", "ok": False, "detail": why}
    install_launcher(cfg, home=home)
    return {"item": "launcher", "ok": True, "fixed": True,
            "detail": f"{why} -> rewritten (takes effect next time the app starts the panel)"}


# ── agy MCP servers ──

def _mcp(cfg, fix, home):
    out = []
    f = _gemini(home) / "mcp_config.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except ValueError:
        return [{"item": "mcp", "ok": False, "detail": f"{f} is not valid JSON — not touched"}]
    servers = data.setdefault("mcpServers", {})
    changed = False

    anki_py = cfg.magistrale / "Agents" / "mcp" / "anki_mcp.py"
    cur = servers.get("anki") or {}
    args = cur.get("args") or []
    if anki_py.is_file() and not (args and Path(args[0]).is_file()):
        if fix:
            servers["anki"] = {**cur, "command": sys.executable, "args": [str(anki_py)], "disabled": False}
            changed = True
            out.append({"item": "anki MCP", "ok": True, "fixed": True,
                        "detail": "missing or pointing at a path that does not exist here -> registered"})
        else:
            out.append({"item": "anki MCP", "ok": False, "detail": "missing or stale path"})
    else:
        out.append({"item": "anki MCP", "ok": anki_py.is_file(),
                    "detail": "registered" if anki_py.is_file() else f"{anki_py} not found"})

    if "obsidian" in servers:
        out.append({"item": "obsidian MCP", "ok": True, "detail": "registered"})
    else:
        key, port = _obsidian_key(cfg)
        if key and fix:
            servers["obsidian"] = {"serverUrl": f"http://127.0.0.1:{port}/mcp/",
                                   "headers": {"Authorization": f"Bearer {key}"}, "disabled": False}
            changed = True
            out.append({"item": "obsidian MCP", "ok": True, "fixed": True,
                        "detail": "registered with the vault's Local REST API key"})
        else:
            out.append({"item": "obsidian MCP", "ok": False,
                        "detail": "not registered" + ("" if key else " (no Local REST API key found in the vault)")})

    if changed:
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.is_file():
            shutil.copy2(f, f.with_suffix(".json.bak"))
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _obsidian_key(cfg):
    f = cfg.vault / ".obsidian" / "plugins" / "obsidian-local-rest-api" / "data.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, 27123
    return d.get("apiKey"), d.get("insecurePort") or 27123


# ── python, workspace, project, git ──

def _deps():
    missing = [m for m in ("numpy", "pypdf") if importlib.util.find_spec(m) is None]
    return {"item": "python packages", "ok": not missing,
            "detail": "numpy, pypdf" if not missing else f"missing: {', '.join(missing)} — pip install {' '.join(missing)}"}


def _workspace(cfg):
    have = [d for d in ("Material", "Graph", "Learner", ".study") if (cfg.magistrale / d).is_dir()]
    ok = len(have) == 4
    return {"item": "workspace", "ok": ok,
            "detail": f"{cfg.magistrale}" + ("" if ok else f" — missing {sorted({'Material', 'Graph', 'Learner', '.study'} - set(have))}; "
                                                             "is Google Drive running and synced?")}


def _project(cfg):
    return {"item": "agy project", "ok": True,
            "detail": cfg.agy_project or "not set: the panel starts conversations in its own project"}


def _git(pull):
    """Only for a development clone (not the deployed copy): fetch, report, optionally fast-forward."""
    if not (config.REPO / ".git").exists() or config._workspace_above(config.REPO):
        return None
    run = lambda *a: subprocess.run(["git", "-C", str(config.REPO), *a], capture_output=True, text=True)  # noqa: E731
    try:
        if run("remote").stdout.strip() == "":
            return {"item": "git", "ok": True, "detail": "no remote"}
        if run("fetch", "--quiet").returncode != 0:
            return {"item": "git", "ok": False, "detail": "fetch failed (offline?)"}
        counts = run("rev-list", "--left-right", "--count", "HEAD...@{upstream}").stdout.split()
    except OSError:
        return {"item": "git", "ok": False, "detail": "git not available"}
    if len(counts) != 2:
        return {"item": "git", "ok": True, "detail": "no upstream branch"}
    ahead, behind = int(counts[0]), int(counts[1])
    if behind and pull:
        dirty = run("status", "--porcelain").stdout.strip()
        if dirty:
            return {"item": "git", "ok": False, "detail": f"{behind} new commit(s) on GitHub; not pulled: local changes"}
        r = run("pull", "--ff-only", "--quiet")
        return {"item": "git", "ok": r.returncode == 0, "fixed": r.returncode == 0,
                "detail": f"pulled {behind} commit(s)" if r.returncode == 0 else "pull failed (diverged?)"}
    return {"item": "git", "ok": not behind,
            "detail": f"{ahead} ahead, {behind} behind GitHub" + (" — run `python study.py sync --pull`" if behind else "")}


def check(cfg, fix=False, pull=False, home=None):
    items = [_workspace(cfg), {"item": "engine", "ok": True, "detail": deploy_stamp(cfg) or "not deployed"},
             _launcher(cfg, fix, home), *_mcp(cfg, fix, home), _deps(), _project(cfg)]
    g = _git(pull)
    if g:
        items.append(g)
    return {"ok": all(i["ok"] for i in items), "items": items}
