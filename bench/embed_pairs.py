import json, sys, time, urllib.request, math
sys.stdout.reconfigure(encoding="utf-8")
MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3-embedding:8b"
def embed(texts):
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/embed", body, {"Content-Type": "application/json"}), timeout=600)
    return json.loads(r.read())["embeddings"]
def cos(a, b):
    return sum(x*y for x, y in zip(a, b)) / (math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b)))

SAME = [  # same idea: paraphrase, IT<->EN, formula<->words
 (r"The graph Laplacian is L = D - A, where D is the degree matrix and A the adjacency matrix.", r"Il Laplaciano di un grafo è la matrice dei gradi meno la matrice di adiacenza."),
 (r"For a connected graph the kernel of L has dimension 1 and is spanned by the all-ones vector.", r"$\dim\ker L = 1$ for a connected graph, with $\ker L = \mathrm{span}(\mathbf{1})$."),
 (r"$x^\top L x = \sum_{(u,v)\in E}(x_u - x_v)^2$", r"The quadratic form of the Laplacian equals the sum over edges of squared differences of the endpoint values."),
 (r"Classical Gram-Schmidt loses orthogonality in floating point arithmetic.", r"In aritmetica finita, Gram-Schmidt classico produce vettori che non sono più ortogonali."),
 (r"The condition number of A is $\kappa(A) = \|A\|\,\|A^{-1}\|$.", r"Il numero di condizionamento di una matrice è il prodotto della norma della matrice per la norma della sua inversa."),
 (r"An adversarial example is an input with a small perturbation crafted to cause misclassification.", r"Un esempio avversario è un input leggermente perturbato, costruito per far sbagliare il classificatore."),
 (r"FGSM perturbs the input by epsilon times the sign of the gradient of the loss with respect to the input.", r"$x' = x + \epsilon\,\mathrm{sign}(\nabla_x J(\theta, x, y))$"),
 (r"In a SYN flood the attacker sends many SYN packets and never completes the three-way handshake, exhausting the server's half-open connection table.", r"Attacco SYN flood: molte richieste SYN senza ACK finale saturano la coda delle connessioni semi-aperte del server."),
 (r"Lloyd's algorithm monotonically decreases the within-cluster sum of squares and terminates in finitely many steps.", r"k-means converge in un numero finito di iterazioni perché la WCSS non aumenta mai e le partizioni sono finite."),
 (r"In MapReduce the shuffle phase groups intermediate key-value pairs by key before the reduce step.", r"La fase di shuffle raggruppa per chiave le coppie intermedie prima della reduce."),
 (r"A symmetric matrix has real eigenvalues and an orthonormal basis of eigenvectors.", r"Teorema spettrale: ogni matrice simmetrica reale è diagonalizzabile tramite una matrice ortogonale, con autovalori reali."),
 (r"Data poisoning attacks corrupt the training set to degrade or control the learned model.", r"Negli attacchi di poisoning l'avversario manipola i dati di addestramento per alterare il modello appreso."),
]
CONF = [  # related, same topic, DIFFERENT idea (must not be merged)
 (r"The graph Laplacian is L = D - A.", r"The normalized Laplacian is $I - D^{-1/2} A D^{-1/2}$."),
 (r"For a connected graph the kernel of L has dimension 1.", r"The number of connected components of a graph equals the multiplicity of the eigenvalue 0 of L."),
 (r"Classical Gram-Schmidt loses orthogonality in floating point arithmetic.", r"Modified Gram-Schmidt projects against each previously computed vector sequentially, which is numerically more stable."),
 (r"The eigenvalues of A are the roots of det(A - λI) = 0.", r"The singular values of A are the square roots of the eigenvalues of $A^\top A$."),
 (r"FGSM is a single-step attack using the sign of the input gradient.", r"PGD is an iterative attack that repeats gradient steps and projects back onto the epsilon ball."),
 (r"An evasion attack perturbs inputs at test time.", r"A poisoning attack manipulates the training data."),
 (r"A SYN flood exhausts the half-open connection table of a server.", r"A Smurf attack floods the victim with ICMP echo replies by spoofing its address to a broadcast address."),
 (r"TLS provides confidentiality and integrity at the transport layer.", r"IPsec provides confidentiality and integrity at the network layer."),
 (r"k-means minimizes the within-cluster sum of squares.", r"k-means++ chooses initial centroids with probability proportional to squared distance from the nearest chosen centroid."),
 (r"The condition number bounds the relative error amplification of a linear system.", r"The spectral radius of the iteration matrix determines convergence of a stationary iterative method."),
 (r"In MapReduce the shuffle groups intermediate pairs by key.", r"A combiner performs local aggregation on the mapper side before the shuffle."),
 (r"Conjugate gradient requires a symmetric positive definite matrix.", r"GMRES applies to general nonsingular matrices and minimizes the residual over a Krylov subspace."),
]
UNREL = [
 (r"The graph Laplacian is L = D - A.", r"A SYN flood exhausts the half-open connection table of a server."),
 (r"FGSM uses the sign of the input gradient.", r"In MapReduce the shuffle groups intermediate pairs by key."),
 (r"TLS provides confidentiality and integrity at the transport layer.", r"k-means minimizes the within-cluster sum of squares."),
 (r"A symmetric matrix has real eigenvalues.", r"A poisoning attack manipulates the training data."),
 (r"The condition number of A is the product of the norms of A and its inverse.", r"ROLLUP computes subtotals along a hierarchy of grouping columns in SQL."),
 (r"Classical Gram-Schmidt loses orthogonality in floating point.", r"IPsec operates at the network layer."),
]
texts = sorted({t for grp in (SAME, CONF, UNREL) for p in grp for t in p})
embed(["warmup"])
t0 = time.time(); vecs = embed(texts); dt = time.time() - t0
V = dict(zip(texts, vecs))
print(f"model {MODEL} | dim {len(vecs[0])} | {len(texts)} texts in {dt:.2f}s = {dt/len(texts)*1000:.0f} ms/text")
out = {}
for name, grp in (("SAME", SAME), ("CONFUSABLE", CONF), ("UNRELATED", UNREL)):
    s = sorted(cos(V[a], V[b]) for a, b in grp); out[name] = s
    print(f"{name:11s} n={len(s):2d} min {s[0]:.3f}  median {s[len(s)//2]:.3f}  max {s[-1]:.3f}")
print("\nlowest SAME pairs:")
for a, b in sorted(SAME, key=lambda p: cos(V[p[0]], V[p[1]]))[:3]: print(f"  {cos(V[a],V[b]):.3f}  {a[:60]} | {b[:60]}")
print("highest CONFUSABLE pairs:")
for a, b in sorted(CONF, key=lambda p: -cos(V[p[0]], V[p[1]]))[:4]: print(f"  {cos(V[a],V[b]):.3f}  {a[:60]} | {b[:60]}")
lo, hi = min(out["SAME"]), max(out["CONFUSABLE"])
both = out["SAME"] + out["CONFUSABLE"]
amb = [x for x in both if min(lo, hi) <= x <= max(lo, hi)] if hi >= lo else []
print(f"\noverlap band [{min(lo,hi):.3f}, {max(lo,hi):.3f}]: {'NO overlap — clean threshold' if hi < lo else str(len(amb)) + ' of ' + str(len(both)) + ' same/confusable pairs fall in it'}")
print(f"max UNRELATED {max(out['UNRELATED']):.3f} vs min SAME {lo:.3f}")
