#!/usr/bin/env python3
"""study-engine — deterministic study loop over Anki + the Obsidian learner record.

  python study.py serve                      start the local engine (the Obsidian Study panel talks to it)
  python study.py review [--deck D] [--limit N]   terminal review (same engine, in-process)
  python study.py decks                      list Anki decks
  python study.py install-plugin             copy the Obsidian plugin into the vault
  python study.py deploy                     copy the engine into Magistrale (Drive) + workspace plugin: all PCs
  python study.py calls [--last N]           show logged model calls (job, model, ok, tokens)

  python study.py ingest <file> [--course C] [--domain D]   material -> proposal (nothing written yet)
  python study.py proposals                  list proposals
  python study.py show <id>                  print one proposal
  python study.py approve <id> [--accept 0,2] [--reject 3]   write items to Graph/ (default: all pending)
  python study.py plan <id>                  build the study plan note for a proposal
  python study.py propose <concepts.json>    concepts taught in an agy session -> proposal (no model call)
  python study.py sync [--pull]              check/repair this PC (launcher, MCP servers, deps); --pull: git

Flags valid everywhere: --stub (offline stand-in grader), --dry-run (never rate/add in Anki).
"""
import argparse
import json
import shutil
import sqlite3
import sys

from engine import config
from engine.anki import AnkiError
from engine.llm import LLMError
from engine.review import SessionError
from engine.server import Engine, serve

for s in (sys.stdout, sys.stderr):
    if hasattr(s, "reconfigure"):
        s.reconfigure(encoding="utf-8")

PLUGIN_ID = "study-panel"


def terminal_review(engine, deck, limit):
    try:
        view = engine.start(deck, limit)
    except AnkiError as e:
        sys.exit(str(e))
    s = engine.session(view.get("session")) if view["state"] != "done" else None
    if not s:
        print("Nothing due.")
        return
    while view["state"] != "done":
        print(f"\n── card {view['position']}/{view['total']} · {view['deck']} · node {view['node']}")
        print(view["front"])
        print("\nAnswer (blank line to finish; Win+H dictates; 'idk' = don't know):")
        lines = []
        while True:
            line = input()
            if not line.strip():
                break
            lines.append(line)
        text = "\n".join(lines)
        idk = text.strip().lower() in ("idk", "non lo so", "i don't know")
        conf = None
        if not idk:
            raw = input("Confidence 0-100 (blank = skip): ").strip()
            conf = int(raw) if raw.isdigit() else None
        try:
            r = s.answer(text, conf, idk)
        except (SessionError, LLMError) as e:
            print(f"!! {e}")
            continue
        print(f"\n== {r['verdict'].upper()} · ease {r['ease']} · miss {r['miss']}"
              + ("  · MISCONCEPTION" if r["misconception"] else ""))
        for it in r["items"]:
            mark = "✓" if it["met"] else "✗"
            print(f"  {mark} {'*' if it['essential'] else ' '} {it['point']}" + (f'  ← "{it["quote"]}"' if it["quote"] else "")
                  + (f"  ({it['note']})" if it["note"] else ""))
        print(f"  {r['feedback']}")
        if r["contradiction_note"]:
            print(f"  false claim: {r['contradiction_note']}")
        print(f"\n-- card answer --\n{r['back']}")
        for rc in r["remedial"]:
            print(f"  + remedial card: {rc['front']}")
        for e in r["errors"]:
            print(f"  ! {e}")
        input("\n[enter] next")
        view = s.next()
    print("\n" + json.dumps(view["summary"], indent=1, ensure_ascii=False))


def install_plugin(cfg):
    src = config.REPO / "obsidian-plugin"
    dst = cfg.vault / ".obsidian" / "plugins" / PLUGIN_ID
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("manifest.json", "main.js", "styles.css"):
        shutil.copy2(src / f, dst / f)
    print(f"installed -> {dst}\nIn Obsidian: Settings → Community plugins → enable 'Study Panel' (reload if listed).")


def install_sidecar(cfg, study_py=None):
    """Install the per-PC launcher plugin `study` in ~/.gemini/config/plugins/study/.

    The Antigravity app only loads side panels from plugins in this global folder (a plugin inside a project's
    .agents/plugins is ignored for panels — tested 2026-11-15), so every PC needs this small manifest once.
    It starts the engine from `study_py`: by default the copy this command is run from. On a second PC, run it
    from the Drive copy:  python "G:\\My Drive\\Università\\Magistrale\\Agents\\study-engine\\study.py" install-sidecar
    Layout mirrors the built-in Swarm plugin: plugin.json · assets/logo.svg · sidecars/panel/sidecar.json.
    """
    from pathlib import Path

    from engine import devicesync
    from engine import sidecar as sc
    study_py = Path(study_py or config.REPO / "study.py")
    root = devicesync.install_launcher(cfg, study_py)        # this PC's Python, the engine copy to run
    m = sc.manifest(cfg, sys.executable, study_py)
    old = Path.home() / ".gemini" / "config" / "sidecars" / "study-engine"     # earlier standalone location
    if (old / "sidecar.json").is_file():
        (old / "sidecar.json").unlink()
        try:
            old.rmdir()
        except OSError:
            pass
    print(f"installed plugin '{sc.PLUGIN_NAME}' -> {root}")
    print(f"  sidecar: {m['command']} {' '.join(m['args'])}")
    print("In the Antigravity app: Customizations -> plugins -> Study (enable), then the + in the side pane.")


DEPLOY_FILES = ["study.py", "requirements.txt", "README.md", "config.example.json"]
DEPLOY_DIRS = ["engine", "sidecar_ui"]


def deploy(cfg):
    """Make the panel available on every PC that syncs the Magistrale folder (Google Drive):

      Magistrale/Agents/study-engine/        runtime copy of the engine (no tests, no git) — synced by Drive
      Magistrale/.agents/plugins/study/      workspace plugin with the sidecar — loaded when the Magistrale
                                             project is open in Antigravity, on any PC
      Magistrale/.study/proposals/           Inbox proposals, shared between PCs
    Development stays in this git repo; run `deploy` again after changing the code.
    """
    import shutil
    import subprocess
    from pathlib import Path

    from engine import sidecar as sc
    if config._workspace_above(config.REPO):
        sys.exit("deploy: run it from the development repo, not from the deployed copy")
    target = cfg.magistrale / "Agents" / "study-engine"
    target.mkdir(parents=True, exist_ok=True)
    for d in DEPLOY_DIRS:
        if (target / d).exists():
            shutil.rmtree(target / d)
        shutil.copytree(config.REPO / d, target / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in DEPLOY_FILES:
        if (config.REPO / f).is_file():
            shutil.copy2(config.REPO / f, target / f)
    try:
        rev = subprocess.run(["git", "-C", str(config.REPO), "describe", "--always", "--dirty"], capture_output=True,
                             text=True).stdout.strip() or "no git"
    except OSError:
        rev = "no git"
    import time
    (target / "DEPLOYED.txt").write_text(f"deployed {time.strftime('%Y-%m-%d %H:%M')} from {config.REPO} ({rev})\n"
                                         "Do not edit here: change the repo and run `python study.py deploy`.\n",
                                         encoding="utf-8")

    # A plugin inside the project's .agents/plugins is not loaded as a side panel by the app: remove the one an
    # earlier deploy created, and point THIS PC's global launcher at the Drive copy instead.
    workspace_plugin = cfg.magistrale / ".agents" / "plugins" / sc.PLUGIN_NAME
    if (workspace_plugin / "plugin.json").is_file():
        shutil.rmtree(workspace_plugin, ignore_errors=True)
        try:
            workspace_plugin.parent.rmdir()                    # only if now empty
        except OSError:
            pass

    moved = 0                                                  # proposals made before they were shared
    old = cfg.data_dir / "proposals"
    if old.is_dir():
        cfg.proposals_dir.mkdir(parents=True, exist_ok=True)
        for f in old.glob("*.json"):
            if not (cfg.proposals_dir / f.name).exists():
                shutil.move(str(f), cfg.proposals_dir / f.name)
                moved += 1

    print(f"engine  -> {target}  ({rev})")
    if moved:
        print(f"moved {moved} proposal(s) to {cfg.proposals_dir}")
    install_sidecar(cfg, target / "study.py")                  # this PC runs the Drive copy, like every other PC
    print(f'Other PCs, once:  python "{target / "study.py"}" install-sidecar')


def show_calls(cfg, last):
    db = sqlite3.connect(str(cfg.db_path))
    rows = db.execute("SELECT id, datetime(ts,'unixepoch','localtime'), session, job, model, ok, usage, duration_ms, "
                      "error FROM llm_calls ORDER BY id DESC LIMIT ?", (last,)).fetchall()
    for r in reversed(rows):
        print(f"#{r[0]} {r[1]} {r[3]:9s} {r[4]:24s} ok={r[5]} {r[7]}ms tokens={r[6]} {r[8] or ''}")


def print_proposal(p):
    st = p["stats"]
    print(f"proposal {p['id']} · {p['status']} · domain {p['domain']} · {p['source']['name']}")
    print(f"  {st['claims']} claims -> {st['concepts']} concepts: {st['new']} new, {st['extends']} extend a note, "
          f"{st['covered']} already covered, {st['merged']} merged · {st['dropped_unquoted']} claims dropped "
          f"(no supporting quote) · {st['uncovered_passages']} passages without claims · {st['seconds']}s")
    lk = st.get("links", {})
    print(f"  links: {lk.get('confident', 0)} confident, {lk.get('model', 0)} model only, "
          f"{lk.get('conflict', 0)} conflicts (left out unless accepted) · {st['pairs_judged']} pairs judged")
    print(f"  tokens: {st['usage']['tokens']}")
    for it in p["items"]:
        c = it["concept"]
        print(f"\n[{it['index']}] {it['decision'].upper():8s} {it['status']:9s} {c['title']}  · {c['kind']} · "
              f"{c['topic']} · {c['anchor'].rsplit('#', 1)[-1]}")
        print(f"     {c['definition']}")
        for x in (it["new_propositions"] if it["decision"] == "extends" else c["propositions"]):
            print(f"       + {x['text']}")
        if it["decision"] == "new":
            print(f"     -> Graph/{p['domain']}/{it['slug']}.md")
        elif it["match"]:
            print(f"     = {it['match']}")
        for e in it["edges"]:
            print(f"     {e['predicate']} -> {e['to']}  [{e['confidence']}] {'; '.join(e['evidence'] + e['conflict'])}")
        for e in it["required_by"]:
            print(f"     prerequisite of <- {e['from']}  [{e['confidence']}] {'; '.join(e['evidence'] + e['conflict'])}")
        for e in it["dropped_edges"]:
            print(f"     (dropped: {e['from']} {e['predicate']} {e['to']} — {e['reason']})")


def ingest_cli(cfg, a):
    from engine import ingest
    from engine.embed import EmbedError, make_embedder
    from engine.llm import make_provider
    from engine.material import MaterialError
    from engine.store import Store
    nums = lambda s: [int(x) for x in s.split(",") if x.strip()] if s else None  # noqa: E731
    try:
        if a.cmd == "proposals":
            for p in ingest.list_proposals(cfg):
                print(f"{p['id']}  {p['status']:8s} pending {p['pending']:3d}  {p['domain']:16s} {p['source']}")
            return
        if not a.target:
            sys.exit(f"{a.cmd}: missing argument")
        if a.cmd == "ingest":
            from engine import learn
            path = ingest.resolve_path(cfg, a.target)
            course = a.course or learn.course_of(cfg, path) or ""
            if course and not (cfg.material / course).is_dir():
                hits = [c for c in learn.courses(cfg) if course.lower() in c.lower()]
                if len(hits) != 1:
                    sys.exit(f"ingest: --course '{course}' matches {len(hits)} course folders")
                course = hits[0]
            domain = a.domain or (learn.default_domain(cfg, cfg.material / course) if course else "")
            if not domain:
                sys.exit("ingest: give --domain (or --course, or a file inside Material/<course>/)")
            store = Store(cfg.db_path)
            seen = {}

            def progress(stage, done, total):
                if seen.get("stage") != stage:
                    seen["stage"] = stage
                    print(f"  {stage}: {total}", flush=True)
            p = ingest.ingest(cfg, store, make_provider(cfg, store), make_embedder(cfg, store),
                              path, domain, progress, course=course)
            return print_proposal(p)
        if a.cmd == "show":
            return print_proposal(ingest.load(cfg, a.target))
        if a.cmd == "approve":
            return print(json.dumps(ingest.apply(cfg, a.target, nums(a.accept), nums(a.reject)), indent=1,
                                    ensure_ascii=False))
        if a.cmd == "plan":
            plan = ingest.build_plan(cfg, a.target)
            print(f"{plan['file']}\n{len(plan['steps'])} steps, {plan['to_study']} not yet held")
            for i, s in enumerate(plan["steps"], 1):
                print(f"  {i:3d}. {s['status']:13s} {s['id']}{'' if s['in_material'] else '  (prerequisite)'}")
    except (MaterialError, EmbedError, LLMError, KeyError) as e:
        sys.exit(f"{a.cmd}: {e}")


def sync_cli(cfg, pull):
    """Check this PC against the synced workspace and repair what lives per PC (see engine/devicesync.py)."""
    from engine import devicesync
    r = devicesync.check(cfg, fix=True, pull=pull)
    for i in r["items"]:
        mark = "fixed" if i.get("fixed") else ("ok" if i["ok"] else "!!")
        print(f"  [{mark:5}] {i['item']:15} {i['detail']}")
    print("this PC is in sync" if r["ok"] else "some items need attention (see !!)")


def propose_cli(cfg, path):
    """Concepts taught in a session (JSON file) -> an Inbox proposal. Exit 2 on invalid input (nothing saved)."""
    from pathlib import Path
    from engine import session
    if not path or not Path(path).is_file():
        sys.exit("usage: python study.py propose <concepts.json>   (format: engine/session.py docstring)")
    try:
        p = session.propose(cfg, json.loads(Path(path).read_text(encoding="utf-8")))
    except (session.SessionError, json.JSONDecodeError) as e:
        print(f"invalid: {e}", file=sys.stderr)
        sys.exit(2)
    s = p["stats"]
    print(f"proposal {p['id']} · domain {p['domain']} · {s['new']} new, {s['extends']} extend a note, "
          f"{s['covered']} already covered")
    for it in p["items"]:
        drops = f"  (dropped links: {', '.join(d['to'] + ' ' + d['reason'] for d in it['dropped_edges'])})" \
            if it["dropped_edges"] else ""
        print(f"  [{it['decision']}, {it['status']}] {it['concept']['title']}  slug: {it['slug']}{drops}")
    print("Use each slug as the cards' lh::<slug> tag. Nothing is in Graph/ until the learner approves it in the "
          "Study panel -> Inbox.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["serve", "review", "decks", "install-plugin", "install-sidecar", "deploy", "calls",
                                    "ingest", "proposals", "show", "approve", "plan", "propose", "sync"])
    ap.add_argument("target", nargs="?", help="file for ingest; proposal id for show/approve/plan")
    ap.add_argument("--domain", help="topic folder for new notes, e.g. deep-nlp (default: from the course)")
    ap.add_argument("--course", help="ingest: the Material course the concepts belong to (a unique part of its name)")
    ap.add_argument("--accept", help="comma-separated item numbers")
    ap.add_argument("--reject", help="comma-separated item numbers")
    ap.add_argument("--deck")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--last", type=int, default=20)
    ap.add_argument("--stub", action="store_true", help="offline deterministic stand-in grader")
    ap.add_argument("--dry-run", action="store_true", help="never rate or add cards in Anki")
    ap.add_argument("--sidecar", action="store_true", help="serve: run as an Antigravity sidecar (port/token from env)")
    ap.add_argument("--pull", action="store_true", help="sync: also fast-forward this git clone from GitHub")
    a = ap.parse_args()

    cfg = config.load()
    if a.stub:
        cfg.provider = "stub"
    if a.dry_run:
        cfg.dry_run = True

    if a.cmd == "install-plugin":
        return install_plugin(cfg)
    if a.cmd == "install-sidecar":
        return install_sidecar(cfg)
    if a.cmd == "deploy":
        return deploy(cfg)
    if a.cmd == "calls":
        return show_calls(cfg, a.last)
    if a.cmd == "propose":
        return propose_cli(cfg, a.target)
    if a.cmd == "sync":
        return sync_cli(cfg, a.pull)
    if a.cmd in ("ingest", "proposals", "show", "approve", "plan"):
        return ingest_cli(cfg, a)
    engine = Engine(cfg)
    if a.cmd == "serve":
        return serve(cfg, engine, as_sidecar=a.sidecar)
    if a.cmd == "decks":
        try:
            print("\n".join(engine.anki.decks()))
        except AnkiError as e:
            sys.exit(str(e))
        return
    if a.cmd == "review":
        terminal_review(engine, a.deck, a.limit)


if __name__ == "__main__":
    main()
