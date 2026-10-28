"""Generate real offline samples with no cloud key, saving local QA evidence."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.learning_engine import generate_content


def main():
    cases = [
        ("Binary search", "Explanation", "Beginner", None),
        ("Binary search", "Revision notes", "Intermediate", 65),
        ("Binary search", "Practice questions", "Advanced", 90),
        ("Python for loops", "Flashcards", "Beginner", 40),
    ]
    records = []
    for topic, kind, level, score in cases:
        started = time.perf_counter()
        timings = []
        def on_text(text):
            if text and not timings:
                timings.append(round(time.perf_counter() - started, 2))
        result = generate_content(topic, kind, level, score, "", "unused",
                                  provider="local", on_text=on_text)
        elapsed = round(time.perf_counter() - started, 2)
        assert result["source"] == "local"
        assert "</think>" not in result["text"]
        records.append({"topic": topic, "kind": kind, "level": level,
                        "first_text_seconds": timings[0], "total_seconds": elapsed, **result})
        print(json.dumps({"level": level, "kind": kind, "first_text_seconds": timings[0], "total_seconds": elapsed, "characters": len(result["text"]), "preview": result["text"][:230]}), flush=True)
    destination = ROOT / ".local-ai" / "verified_samples.json"
    destination.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print("All four offline generation cases completed. Samples: .local-ai/verified_samples.json", flush=True)


if __name__ == "__main__":
    main()
