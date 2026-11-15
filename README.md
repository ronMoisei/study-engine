# study-engine

Deterministic study engine over an Obsidian vault and Anki. **Models only fill small, checked forms; code decides
everything else** — what comes next, verdicts, ratings, what enters the graph, the study order.

UI: the "Study" side panel in Obsidian (`obsidian-plugin/`). Data: the vault in `Magistrale/Obsidian Notes`.

## 1. Ingest material → proposal → graph → study plan

```bash
python study.py ingest "01VIXWS - Deep natural language processing/01_Study/Slides/6-AttentionMechanism-Cagliero.pdf" --domain deep-nlp
python study.py proposals
python study.py show <id>
python study.py approve <id> [--accept 0,2] [--reject 3]     # default: every pending item
python study.py plan <id>
```

| Step | Who | What |
|---|---|---|
| parse | code | file → passages with page/line anchors (`.pdf`, `.md`, `.txt`) |
| extract | model, cached per passage | atomic claims, each with a verbatim quote |
| verify | code | a claim whose quote is not in the passage is dropped |
| retrieve | local embeddings | nearest existing nodes, plus claims already accepted in this run |
| judge | local model | "same idea?" per candidate; below `sim_floor` it is new with no call |
| link | model, one call per 20 new nodes | proposes `requires` / `contrasts_with` / `analogue_of` |
| check | code | drops unknown ids, loops in the prerequisite graph, edits to read-only notes |
| approve | you | Inbox in the panel, or `approve`; only then are notes written to `Graph/<domain>/` |
| plan | code | prerequisite closure of the material, dependency order, status from the evidence log |

The node index is built on every run from markdown: `Graph/**.md` (writable) and course `*/atomic/*.md`
(read-only; `prerequisites` / `implications` become `requires` edges). Nothing is stored that cannot be rebuilt.

### Model assignment (config `models`, format `provider:model`)

| job | default | why |
|---|---|---|
| extract | `agy:gemini-3.8-flash-low` | local models split procedures into fragments; cached, so paid once per file |
| judge | `ollama:qwen3.5:9b` | 24/24 on `bench/judge_pairs.py`, ~370 ms, free |
| link | `agy:gemini-3.8-flash-low` | local models get prerequisite direction backwards (general vs specific) |
| embeddings | `qwen3-embedding:8b` (Ollama) | ~27 ms/text, free |
| checklist, grade, remedial | agy flash | not yet compared with local models |

Measured on a 37-page slide deck: 12 claims, 4 matched existing notes, 8 new, 15 links;
about 37k cloud input tokens for extraction (3 calls) and 14.5k for linking (1 call); 84 s cold, 31 s cached.
**agy adds roughly 13k input tokens per call** (its own system prompt), so the engine sends few, large calls:
6000-character passages for cloud extraction, one batched link call.

Thresholds (`sim_floor` 0.45) come from `bench/embed_pairs.py`: unrelated pairs ≤ 0.35, same-idea pairs ≥ 0.65,
but "same" and "related but different" overlap (0.57–0.93), which is why a judge decides, not the similarity.
The bench set is 30 hand-written pairs; grow it from real notes before trusting the numbers further.

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
