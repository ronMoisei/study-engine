import json
import unittest

from engine import grading
from engine.llm import find_conforming, find_usage, validate

CL = [{"point": "L = D - A", "essential": True},
      {"point": "D is the diagonal degree matrix", "essential": True},
      {"point": "rows of L sum to zero", "essential": False}]


def items(*met):
    return [{"point": p["point"], "essential": p["essential"], "met": m, "quote": "", "note": ""} for p, m in zip(CL, met)]


class Decide(unittest.TestCase):
    def test_correct_confident_all_met_is_easy(self):
        d = grading.decide(items(True, True, True), 95, False, False, "none")
        self.assertEqual((d["verdict"], d["ease"], d["miss"]), ("correct", 4, None))

    def test_correct_missing_nonessential_is_good(self):
        self.assertEqual(grading.decide(items(True, True, False), 95, False, False, "none")["ease"], 3)

    def test_correct_low_confidence_is_hard(self):
        d = grading.decide(items(True, True, True), 30, False, False, "none")
        self.assertEqual((d["verdict"], d["ease"]), ("correct", 2))

    def test_missing_confidence_counts_as_50(self):
        self.assertEqual(grading.decide(items(True, True, True), None, False, False, "none")["ease"], 3)

    def test_partial(self):
        d = grading.decide(items(True, False, True), 80, False, False, "execution")
        self.assertEqual((d["verdict"], d["ease"], d["miss"]), ("partial", 2, "execution"))

    def test_wrong_high_confidence_is_misconception(self):
        d = grading.decide(items(False, False, False), 85, False, False, "cue")
        self.assertEqual((d["verdict"], d["ease"], d["miss"], d["misconception"]), ("wrong", 1, "structural", True))

    def test_wrong_low_confidence_keeps_grader_class(self):
        d = grading.decide(items(False, False, False), 20, False, False, "discrimination")
        self.assertEqual((d["miss"], d["misconception"]), ("discrimination", False))

    def test_contradiction_downgrades_correct(self):
        d = grading.decide(items(True, True, True), 90, False, True, "structural")
        self.assertEqual((d["verdict"], d["ease"], d["miss"]), ("partial", 2, "structural"))

    def test_idk(self):
        d = grading.decide(items(False, False, False), None, True, False, "cue")
        self.assertEqual((d["verdict"], d["ease"]), ("idk", 1))

    def test_deterministic(self):
        args = (items(True, False, True), 70, False, False, "execution")
        self.assertEqual(grading.decide(*args), grading.decide(*args))


class Quotes(unittest.TestCase):
    ANSWER = "Well, the Laplacian is L = D minus A... no wait, L = D - A, where D is the degree matrix."

    def test_verbatim(self):
        self.assertTrue(grading.quote_supported("L = D - A", self.ANSWER))

    def test_case_and_punctuation_insensitive(self):
        self.assertTrue(grading.quote_supported("where d is the Degree Matrix", self.ANSWER))

    def test_invented_quote_rejected(self):
        self.assertFalse(grading.quote_supported("rows of L sum to zero", self.ANSWER))

    def test_empty_quote_rejected(self):
        self.assertFalse(grading.quote_supported("", self.ANSWER))

    def test_verify_downgrades_unsupported_and_missing(self):
        graded = {"items": [{"index": 0, "met": True, "evidence_quote": "L = D - A"},
                            {"index": 1, "met": True, "evidence_quote": "D is diagonal with vertex degrees"}]}
        out = grading.verify_items(CL, graded, self.ANSWER)
        self.assertEqual([o["met"] for o in out], [True, False, False])
        self.assertIn("not in the answer", out[1]["note"])
        self.assertIn("skipped", out[2]["note"])


class Envelope(unittest.TestCase):
    def test_finds_schema_object_inside_unknown_envelope(self):
        env = {"type": "result", "usage": {"input_tokens": 812, "output_tokens": 95},
               "result": "```json\n" + json.dumps({"points": [{"point": "x", "essential": True}]}) + "\n```"}
        self.assertEqual(find_conforming(env, grading.CHECKLIST_SCHEMA), {"points": [{"point": "x", "essential": True}]})
        self.assertEqual(find_usage(env), {"input_tokens": 812, "output_tokens": 95})

    def test_rejects_nonconforming(self):
        self.assertIsNone(find_conforming({"result": '{"points": []}'}, grading.CHECKLIST_SCHEMA))

    def test_validate_enum_and_bool_vs_int(self):
        bad = {"items": [{"index": True, "met": True, "evidence_quote": ""}], "contradiction": False,
               "contradiction_note": "", "miss_type": "nope", "feedback": ""}
        errs = validate(bad, grading.GRADE_SCHEMA)
        self.assertTrue(any("expected integer" in e for e in errs))
        self.assertTrue(any("not in" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
