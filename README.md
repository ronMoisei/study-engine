# study-engine

Deterministic study engine over an Obsidian vault and Anki. **Models only fill small, checked forms; code decides
everything else** — what comes next, verdicts, ratings, what enters the graph, the study order.

UI: the "Study" side panel in the Antigravity app (`sidecar_ui/`, installed with `python study.py deploy`; the old
Obsidian panel in `obsidian-plugin/` is superseded). Data: `Magistrale/` is the Obsidian vault — `Graph/` (concepts),
`Learner/` (plans, sessions, `evidence.jsonl`), `Material/<course>/Atomic Notes/` (read-only course nodes).

Picking a topic in the panel (Graph drawer or Inbox) takes a self-rated level 1–5 and an optional goal/scope; the
level chooses the session mode (1 teach from scratch · 2 probe then teach · 3 evaluation · 4 exam · 5 project). Math
renders with the vendored KaTeX 0.16.11 (`sidecar_ui/vendor/katex`, MIT).

## 1. Ingest material → proposal → graph → study plan

```bash
python study.py ingest "01VIXWS - Deep natural language processing/01_Study/Slides/6-AttentionMechanism-Cagliero.pdf" --domain deep-nlp
python study.py proposals
python study.py show <id>
python study.py approve <id> [--accept 0,2] [--reject 3]     # default: every pending item
python study.py plan <id>
```

The graph is built over **concepts** (one teachable unit each: definition + propositions), not single claims.

| Step | Who | What |
|---|---|---|
| parse | code | file → passages; each PDF page / text paragraph keeps its own anchor |
| extract | model, cached per passage | claims, each with a verbatim quote |
| verify | code | a claim whose quote is not in the passage is dropped |
| group | model, one call per file, cached | claims → concepts (title, definition, propositions, topic) |
| pairs | local embeddings | each concept + its nearest notes above `sim_floor` |
| decide | model, one call per 60 pairs | per pair: same concept / A requires B / B requires A / contrasts / analogue / none |
| resolve | code | "same" → adds to or is covered by an existing note, or merges into an earlier concept |
| veto | code | loops; prerequisite appearing **later** in the same course/file; later topic; definition requiring an application → *conflict* |
| shortcuts | code | A→C dropped when A→…→C exists |
| approve | you | Inbox: concepts and every link individually; conflicts start unticked |
| write | code | `engine/notes.py` renders the one concept format and validates it before every write |
| plan | code | dependency order + "term used before it is taught" check |

Links are always stored in the **new** note (`Requires`, or `Prerequisite of` for "that note requires this one"),
so course notes are never edited. Topics per domain live in `Graph/<domain>/_topics.md`, an ordered list you can
reorder by hand; the order is used as a veto.

Why vetoes instead of free signals deciding links (measured on the 196 course notes): "B's text mentions A" points
against the existing links as often as along them, so no free signal decides direction. Order is a reliable veto:
of 157 existing links with positions, none has the prerequisite in a later lecture.

### Model assignment (config `models`, format `provider:model`)

| job | default | why |
|---|---|---|
| extract | `agy:gemini-3.8-flash-low` | local models split procedures into fragments; cached, so paid once per file |
| group | `agy:gemini-3.8-flash-low` | one call per file, cached |
| relate | `agy:gemini-3.8-flash-low` | identity + direction; the local judge merged general concepts into specific ones |
| judge | `ollama:qwen3.5:9b` | only for "is this proposition already in the note?"; 24/24 on `bench/judge_pairs.py` |
| embeddings | `qwen3-embedding:8b` (Ollama) | ~27 ms/text, free |
| checklist, grade, remedial | agy flash | not yet compared with local models |

Measured on a 37-page slide deck: 12 claims → 8 concepts (6 new, 2 already covered), 58 pairs decided in one call
(~21k cloud tokens), 15 links (3 confident, 11 model only, 1 conflict), 13 shortcut links removed.
**agy adds roughly 10–13k input tokens per call** (its own system prompt), so the engine sends few, large calls.
Prompts go to agy on stdin (stream-json): a command-line argument is capped at ~32k characters on Windows.

Thresholds (`sim_floor` 0.45) come from `bench/embed_pairs.py`: unrelated pairs ≤ 0.35, same-idea pairs ≥ 0.65,
but "same" and "related but different" overlap (0.57–0.93), which is why a judge decides, not the similarity.
The bench set is 30 hand-written pairs; grow it from real notes before trusting the numbers further.

## Side panel in the Antigravity app (the UI)

```bash
python study.py deploy               # runtime copy -> Magistrale/Agents/study-engine (Drive) + this PC's launcher
```

On every other PC, once (from the Drive copy):

```bash
python "G:\My Drive\Università\Magistrale\Agents\study-engine\study.py" install-sidecar
pip install numpy pypdf
```

The app only loads side panels from plugins in the global `~/.gemini/config/plugins/` folder (a plugin inside a
project's `.agents/plugins` is ignored for panels), so each PC gets a small launcher plugin `study` that starts the
engine from the shared Drive copy. Proposals live in `Magistrale/.study/` and are shared; caches stay per PC.
Don't use the panel on two PCs at the same moment (both would write the same Drive files). The app starts
`python study.py serve --sidecar`, passing the port and an access token; the panel (`sidecar_ui/`, plain JS, no
libraries) opens in the side pane. Every API call needs the token; static UI files don't. No CORS headers, loopback
only — remote access goes through the app's own gateway.

- **Graph** — the prerequisite DAG per domain/course, laid out deterministically (`engine/layout.py`: level =
  longest path from a foundation, crossings reduced by barycenter sweeps; foundations at the bottom). Colours come
  from the evidence log. Click a node: definition, requires / needed by, study path, *Show path*, *Open in
  Obsidian*, and **Teach me this**, which sends a message built from the graph (`graphview.teach_message`) into
  the conversation the panel sits next to, via `agentapi`.
- **Inbox** — add material, review proposals (concepts + per-link checkboxes), build study plans.
- **Review** — the Anki loop.

Formulas are shown as LaTeX source until KaTeX is vendored into `sidecar_ui/`.

## 2. Anki review loop

1. Engine fetches due cards from AnkiConnect and returns **the front only**.
2. You answer in the panel (type, or dictate with Win+H); confidence is optional (unset is not 50).
3. **checklist** (once per card, cached): 1–5 checkpoints from the card's back.
4. **grade**: met / not met per checkpoint, each "met" with a verbatim quote from your answer.
5. Code rejects any "met" whose quote isn't in your answer; `grading.decide()` computes verdict + ease.
6. Evidence is appended to `Learner/evidence.jsonl` **before** the card is rated in Anki.
7. On a miss: 1–2 remedial cards, due today, tagged `remedial gap::<node>`.

## Run

```bash
python study.py serve                  # engine for the Obsidian panel (the panel can auto-start it)
python study.py review --deck "Graph"  # terminal review (LaTeX shows as source)
python study.py calls --last 20        # every model call: job, model, ok, tokens, duration
python study.py install-plugin         # copy obsidian-plugin/ into the vault
python -m unittest discover -s tests -t .
```

Flags: `--stub` (offline stand-in models, tests only) · `--dry-run` (never rate/add in Anki).
Needs: Ollama running for ingest; desktop Anki with AnkiConnect for review; `agy` for the cloud jobs.

## Files

- `engine/` — `material` (parsing), `graph` (index, loops, notes), `embed`, `ingest` (pipeline, proposals, plan),
  `learner` (status from evidence), `llm` (agy / Ollama / stub providers and routing), `grading`, `review`,
  `anki`, `store` (SQLite cache and call log in `%LOCALAPPDATA%\study-engine`; safe to delete), `server`.
- `obsidian-plugin/` — plain-JS plugin, no build step.
- `bench/` — the pair sets used to pick the embedding threshold and the judge model.
- `config.example.json` — copy to `config.json` to override defaults.

## Not built yet

Flashcards from graph nodes, flashcard vectors (duplicates, coverage, weak-topic clusters), keyword search
beside embeddings, the probe / teach / practice loops in the panel.
