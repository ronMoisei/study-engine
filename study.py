#!/usr/bin/env python3
"""study-engine — deterministic study loop over Anki + the Obsidian learner record.

  python study.py serve                      start the local engine (the Obsidian Study panel talks to it)
  python study.py review [--deck D] [--limit N]   terminal review (same engine, in-process)
  python study.py decks                      list Anki decks
  python study.py install-plugin             copy the Obsidian plugin into the vault
  python study.py calls [--last N]           show logged model calls (job, model, ok, tokens)

  python study.py ingest <file> --domain D   material -> proposal (nothing is written to the vault yet)
  python study.py proposals                  list proposals
  python study.py show <id>                  print one proposal
  python study.py approve <id> [--accept 0,2] [--reject 3]   write items to Graph/ (default: all pending)
  python study.py plan <id>                  build the study plan note for a proposal

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


def show_calls(cfg, last):
    db = sqlite3.connect(str(cfg.db_path))
    rows = db.execute("SELECT id, datetime(ts,'unixepoch','localtime'), session, job, model, ok, usage, duration_ms, "
                      "error FROM llm_calls ORDER BY id DESC LIMIT ?", (last,)).fetchall()
    for r in reversed(rows):
        print(f"#{r[0]} {r[1]} {r[3]:9s} {r[4]:24s} ok={r[5]} {r[7]}ms tokens={r[6]} {r[8] or ''}")


def print_proposal(p):
    st = p["stats"]
    print(f"proposal {p['id']} · {p['status']} · domain {p['domain']} · {p['source']['name']}")
    print(f"  {st['claims']} claims: {st['new']} new, {st['existing']} already in the graph, "
          f"{st['duplicates_in_material']} repeated · {st['dropped_unquoted']} dropped (no supporting quote) · "
          f"{st['uncovered_passages']} passages without claims · {st['seconds']}s")
    print(f"  tokens: {st['usage']['tokens']}")
    for it in p["items"]:
        c = it["claim"]
        tag = {"new": "NEW     ", "existing": "EXISTING", "duplicate": "REPEATED"}[it["decision"]]
        print(f"\n[{it['index']}] {tag} {it['status']:9s} {c['name']}  ({c['anchor'].rsplit('#', 1)[-1]})")
        print(f"     {c['statement']}")
        if it["decision"] == "new":
            print(f"     -> Graph/{p['domain']}/{it['slug']}.md")
        else:
            print(f"     = {it['match']}")
        for e in it["edges"]:
            print(f"     {e['predicate']} -> {e['to']}")
        for e in it["incoming"]:
            print(f"     {e['from']} {e['predicate']} -> this")
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
            if not a.domain:
                sys.exit("ingest: --domain is required (e.g. --domain deep-nlp)")
            store = Store(cfg.db_path)
            seen = {}

            def progress(stage, done, total):
                if seen.get("stage") != stage:
                    seen["stage"] = stage
                    print(f"  {stage}: {total}", flush=True)
            p = ingest.ingest(cfg, store, make_provider(cfg, store), make_embedder(cfg, store),
                              ingest.resolve_path(cfg, a.target), a.domain, progress)
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["serve", "review", "decks", "install-plugin", "calls",
                                    "ingest", "proposals", "show", "approve", "plan"])
    ap.add_argument("target", nargs="?", help="file for ingest; proposal id for show/approve/plan")
    ap.add_argument("--domain", help="topic folder for new notes, e.g. deep-nlp")
    ap.add_argument("--accept", help="comma-separated item numbers")
    ap.add_argument("--reject", help="comma-separated item numbers")
    ap.add_argument("--deck")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--last", type=int, default=20)
    ap.add_argument("--stub", action="store_true", help="offline deterministic stand-in grader")
    ap.add_argument("--dry-run", action="store_true", help="never rate or add cards in Anki")
    a = ap.parse_args()

    cfg = config.load()
    if a.stub:
        cfg.provider = "stub"
    if a.dry_run:
        cfg.dry_run = True

    if a.cmd == "install-plugin":
        return install_plugin(cfg)
    if a.cmd == "calls":
        return show_calls(cfg, a.last)
    if a.cmd in ("ingest", "proposals", "show", "approve", "plan"):
        return ingest_cli(cfg, a)
    engine = Engine(cfg)
    if a.cmd == "serve":
        return serve(cfg, engine)
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
