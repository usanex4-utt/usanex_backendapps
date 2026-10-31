
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

DATASET_FILE = DATA_DIR / "nexa_dataset.jsonl"
KNOWLEDGE_FILE = DATA_DIR / "usanex_knowledge.jsonl"


def merge_knowledge():
    if not DATASET_FILE.exists():
        print(f"Dataset nahi mila: {DATASET_FILE}")
        return

    if not KNOWLEDGE_FILE.exists():
        print(f"Knowledge file nahi mili: {KNOWLEDGE_FILE}")
        return

    existing = []
    seen = set()

    for path in (DATASET_FILE, KNOWLEDGE_FILE):
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue

                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    print(f"Invalid JSON: {path.name}, line {line_number}")
                    continue

                question = str(item.get("question", "")).strip()
                answer = str(item.get("answer", "")).strip()

                if not question or not answer:
                    continue

                key = (question.casefold(), answer.casefold())
                if key in seen:
                    continue

                seen.add(key)
                existing.append({
                    "question": question,
                    "answer": answer,
                    "topic": item.get("topic", "general")
                })

    with DATASET_FILE.open("w", encoding="utf-8") as file:
        for item in existing:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"Merge complete. Total examples: {len(existing)}")


if __name__ == "__main__":
    merge_knowledge()
