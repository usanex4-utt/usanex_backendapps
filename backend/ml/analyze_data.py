import json
from pathlib import Path
from collections import Counter

DATA_FILE = Path(__file__).resolve().parent / "data" / "nexa_dataset.jsonl"


def analyze_dataset():
    if not DATA_FILE.exists():
        print(f"Dataset nahi mila: {DATA_FILE}")
        return

    total = 0
    topics = Counter()
    invalid = 0

    with DATA_FILE.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            try:
                item = json.loads(line)
                question = item.get("question", "")
                answer = item.get("answer", "")

                if not question or not answer:
                    invalid += 1
                    continue

                total += 1
                topics[item.get("topic", "general")] += 1

            except json.JSONDecodeError:
                invalid += 1
                print(f"Invalid JSON: line {line_number}")

    print("\n--- NEXA Dataset Report ---")
    print(f"Valid examples: {total}")
    print(f"Invalid examples: {invalid}")
    print("\nExamples by topic:")

    for topic, count in topics.most_common():
        print(f"{topic}: {count}")


if __name__ == "__main__":
    analyze_dataset()