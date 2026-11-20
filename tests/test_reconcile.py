"""Repeat check: deterministic candidates for each draft, then the model's decision with code checks."""
import unittest

from engine import reconcile

POOL = [{"id": "gradient_descent", "title": "Gradient Descent", "aliases": ["GD"], "kind": "algorithm",
         "topics": ["optimization/first-order"], "definition": "Iteratively step against the gradient of the loss.",
         "propositions": ["The learning rate sets the step size."]},
        {"id": "kmeans", "title": "K-Means Clustering", "aliases": [], "kind": "algorithm",
         "topics": ["machine-learning/clustering"], "definition": "Assign points to the nearest centroid, then update.",
         "propositions": []}]


def draft(key, title, definition, topics=("optimization/first-order",)):
    return {"key": key, "title": title, "kind": "algorithm", "topics": list(topics), "definition": definition,
            "aliases": [], "propositions": ["Momentum accelerates it."]}


class Candidates(unittest.TestCase):
    def test_title_alias_and_definition_overlap(self):
        c = reconcile.candidates([draft("n1", "Gradient descent", "Move parameters opposite to the gradient."),
                                  draft("n2", "Stochastic step", "Iteratively step against the gradient of the loss."),
                                  draft("n3", "Attention", "Weights over the tokens of a sequence.")], POOL)
        self.assertEqual([p["id"] for p, _ in c["n1"]], ["gradient_descent"])        # same title
        self.assertEqual([p["id"] for p, _ in c["n2"]], ["gradient_descent"])        # same definition words
        self.assertNotIn("n3", c)                                                      # nothing alike: no model call
        self.assertEqual(reconcile.candidates([draft("n1", "x", "y")], []), {})


class Decide(unittest.TestCase):
    def test_target_must_be_a_candidate_and_empty_integrate_is_skip(self):
        d = [draft("n1", "Gradient descent", "Move parameters opposite to the gradient.")]
        cands = reconcile.candidates(d, POOL)

        def cached(*a, **k):
            return {"decisions": [{"key": "n1", "action": "integrate", "target": "gradient_descent", "add": [],
                                   "why": "same"}, {"key": "zz", "action": "skip", "target": "kmeans", "add": [],
                                                    "why": "not a draft"}]}
        self.assertEqual(reconcile.decide(None, None, None, d, cands, "s", cached)["n1"]["action"], "skip")

        def wrong(*a, **k):
            return {"decisions": [{"key": "n1", "action": "skip", "target": "kmeans", "add": [], "why": "x"}]}
        self.assertEqual(reconcile.decide(None, None, None, d, cands, "s", wrong)["n1"]["action"],
                         "integrate")      # kmeans was not a candidate -> "new", but "new" may not share a title

    def test_same_title_needs_a_distinct_title_to_stay_new(self):
        d = [draft("n1", "Gradient descent", "Move parameters opposite to the gradient.")]
        cands = reconcile.candidates(d, POOL)

        def new(retitle):
            return lambda *a, **k: {"decisions": [{"key": "n1", "action": "new", "target": "", "add": [], "why": "x",
                                                   "retitle": retitle}]}
        r = reconcile.decide(None, None, None, d, cands, "s", new("Gradient Descent in scikit-learn"))["n1"]
        self.assertEqual((r["action"], r["retitle"]), ("new", "Gradient Descent in scikit-learn"))
        r = reconcile.decide(None, None, None, d, cands, "s", new(""))["n1"]
        self.assertEqual((r["action"], r["target"]), ("integrate", "gradient_descent"))   # merged, not a twin title


if __name__ == "__main__":
    unittest.main()
