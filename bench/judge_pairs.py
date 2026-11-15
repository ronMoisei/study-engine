import json, sys, time, urllib.request, importlib.util
sys.stdout.reconfigure(encoding="utf-8")
spec = importlib.util.spec_from_file_location("et", sys.argv[1])
src = open(sys.argv[1], encoding="utf-8").read().split("texts = sorted")[0]
ns = {}; exec(src, ns)
SAME, CONF = ns["SAME"], ns["CONF"]
SCHEMA = {"type": "object", "properties": {"same_idea": {"type": "boolean"}}, "required": ["same_idea"]}
PROMPT = """Two statements from study notes. Decide whether they state the SAME single idea.
same_idea = true only if B is a paraphrase, translation, or notation variant of A: someone who knows A already knows B, with nothing new learned.
same_idea = false if B is a different fact, even one on the same topic, a related method, a generalization, or a consequence.
A: {a}
B: {b}
Answer with JSON only."""
def judge(model, a, b):
    body = {"model": model, "stream": False, "format": SCHEMA, "think": False,
            "options": {"temperature": 0, "seed": 7, "num_predict": 40},
            "messages": [{"role": "user", "content": PROMPT.format(a=a, b=b)}]}
    try:
        r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=300)
    except urllib.error.HTTPError as e:
        if e.code == 400:   # model without thinking support rejects the flag
            body.pop("think")
            r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=300)
        else: raise
    return json.loads(json.loads(r.read())["message"]["content"])["same_idea"]
pairs = [(a, b, True) for a, b in SAME] + [(a, b, False) for a, b in CONF]
for model in sys.argv[2:]:
    judge(model, "warm", "up")
    runs = []
    t0 = time.time()
    for _ in range(2):
        runs.append([judge(model, a, b) for a, b, _ in pairs])
    dt = (time.time() - t0) / (2 * len(pairs))
    got = runs[0]
    tp = sum(g and w for g, (_, _, w) in zip(got, pairs)); tn = sum((not g) and (not w) for g, (_, _, w) in zip(got, pairs))
    print(f"\n{model}: {tp+tn}/24 correct | same recognized {tp}/12 | different kept apart {tn}/12 | {dt*1000:.0f} ms/pair | identical on rerun: {runs[0] == runs[1]}")
    for g, (a, b, w) in zip(got, pairs):
        if g != w: print(f"   WRONG ({'merged two ideas' if g else 'missed a duplicate'}): {a[:55]} | {b[:55]}")
