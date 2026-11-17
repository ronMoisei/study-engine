"""Create raw notes: the offline pipeline run from the panel, progress parsed from its output, one update per lecture."""
import sys
import textwrap
import unittest
from unittest import mock

from engine import rawnotes
from tests.test_ingest import make_env

ENHANCE = '''
print("1 lectures already have enhanced audio; 2 to do; 0 duplicate copies skipped")
print("  - [01ABCDE] L1.mp4  ->  Audio/L1_enhanced.mp3")
print("[1/2] OK [01ABCDE] L1.mp4 (medium, 50.0 MB, 29s)")
print("[2/2] FAILED [01ABCDE] L2.mp4: Audio processing failed")
print("ENHANCE DONE: 1 ok, 1 failed, 1 min")
'''
INGEST = '''
import sys
print("ARGS", " ".join(sys.argv[1:]))
print("=" * 80)
print(" FULL BATCH RUN: 2 genuinely pending lectures found across all courses.")
print(" BATCH PROGRESS: [1/2] 01ABCDE - L1")
print(">>> PHASE 1: Speech Transcription (faster-whisper GPU)")
print("[Audio] Found existing DSP enhanced audio: L1_enhanced.mp3")
print("[Audio] Detected language: 'en' (probability 1.00), Duration: 3600.0s")
for k in range(1, 12):
    print(f"  [{k}/11] 00:0{k % 10}:00 -> frame.png: slide x.pdf p.{k}, no ink")
print(">>> PHASE 4: Chronological Synthesis & Note Export")
print(" Total Keyframes: 11")
print(" Total Audio Segments: 380")
print(" BATCH PROGRESS: [2/2] 01ABCDE - L2")
print(">>> PHASE 1: Speech Transcription (faster-whisper GPU)")
print("[ERROR] Failed processing L2.mp4: CUDA error. Continuing to next lecture...")
print(" FULL BATCH COMPLETE: All lectures processed!")
'''


def pipeline_env():
    cfg, store, llm, emb, _ = make_env()
    folder = cfg.magistrale / "pipeline"
    folder.mkdir()
    (folder / "ingest.py").write_text(textwrap.dedent(INGEST), encoding="utf-8")
    (folder / "enhance_all.py").write_text(textwrap.dedent(ENHANCE), encoding="utf-8")
    cfg.lecture_pipeline, cfg.lecture_python = str(folder), sys.executable
    return cfg, llm


class CreateRawNotes(unittest.TestCase):
    def test_progress_and_one_update_per_lecture(self):
        cfg, llm = pipeline_env()
        r = rawnotes.RawNotesRun(cfg, llm)
        with mock.patch.object(rawnotes, "start_ollama", return_value=True):
            r.run()
        st = r.view()
        self.assertEqual(st["state"], "error")                    # one audio failure + one lecture failure
        self.assertEqual((st["phase"], st["done"], st["total"]), ("lectures", 2, 2))
        texts = [u["text"] for u in st["updates"]]
        self.assertTrue(texts[0].startswith("Starting"))
        self.assertIn("Audio: 2 recordings to clean up (1 already done).", texts)
        self.assertTrue(any("Whisper will use its raw audio" in t for t in texts))
        lectures = [u for u in st["updates"] if u["lecture"]]
        self.assertEqual([(u["lecture"], u["kind"]) for u in lectures], [("L1", "lecture"), ("L2", "error")])
        self.assertEqual(lectures[0]["text"], "L1: lecture finished.")        # the stub narrator's answer
        self.assertTrue(texts[-1].startswith("Finished: 2 lectures processed"))
        self.assertTrue(any("CUDA error" in e for e in st["errors"]))
        with open(st["log"], encoding="utf-8") as fh:
            self.assertIn("Total Audio Segments: 380", fh.read())         # the full log is kept

    def test_one_course_and_only_when_there_is_something_new(self):
        from engine.material import MaterialError
        from engine.server import Engine
        cfg, llm = pipeline_env()
        course = cfg.material / "01ABCDE - Optimization"
        (course / "01_Study" / "Lectures").mkdir(parents=True)
        engine = Engine(cfg, anki=object(), llm=llm)
        with self.assertRaises(MaterialError):                     # no recording without raw notes: no run
            engine.start_raw_notes(course.name)
        (course / "01_Study" / "Lectures" / "L1.mp4").write_bytes(b"x")
        r = rawnotes.RawNotesRun(cfg, llm, course.name)
        self.assertEqual(r.left(), 1)
        with mock.patch.object(rawnotes, "start_ollama", return_value=True):
            r.run()
        self.assertEqual(r.view()["scope"], course.name)
        with open(r.view()["log"], encoding="utf-8") as fh:
            self.assertIn("ARGS --all-courses --yes --course 01ABCDE", fh.read())

    def test_facts_and_excerpt(self):
        lines = ["[Audio] Detected language: 'en' (probability 1.00), Duration: 3600.0s",
                 " Total Keyframes: 11", " Total Audio Segments: 380"] + [f"  [{k}/40] frame" for k in range(1, 41)]
        f = rawnotes.facts(lines)
        self.assertEqual((f["segments"], f["keyframes"], f["language"], f["cleaned_audio"]), ("380", "11", "en", False))
        self.assertEqual(rawnotes.plain(f, "L1"), "L1: 60 min, 380 speech segments, 11 keyframes.")
        ex = rawnotes.excerpt(lines)
        self.assertIn("40 keyframe lines in total", ex)
        self.assertNotIn("[20/40]", ex)

    def test_model_failure_keeps_the_facts(self):
        cfg, llm = pipeline_env()

        class Down:
            def run(self, *a, **k):
                raise RuntimeError("quota")
        r = rawnotes.RawNotesRun(cfg, Down())
        with mock.patch.object(rawnotes, "start_ollama", return_value=True):
            r.run()
        l1 = [u for u in r.view()["updates"] if u["lecture"] == "L1"][0]
        self.assertTrue(l1["text"].startswith("L1: 60 min, 380 speech segments, 11 keyframes."))


if __name__ == "__main__":
    unittest.main()
