
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

DATA_FILE = DATA_DIR / "train.jsonl"
MODEL_FILE = DATA_DIR / "nexa_model.json"


def tokenize(text):
    return re.findall(r"[a-z0-9@']+", text.lower())


def train():
    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"Training data nahi mila: {DATA_FILE}"
        )

    examples = []

    with DATA_FILE.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSON at line {line_number}"
                ) from error

            if item.get("question") and item.get("answer"):
                examples.append(item)

    if not examples:
        raise ValueError("Training dataset khali hai.")

    topic_docs = Counter()
    word_topic = defaultdict(Counter)
    topic_answers = defaultdict(Counter)
    vocabulary = set()

    for item in examples:
        topic = item.get("topic", "general")
        words = set(tokenize(item["question"]))
        topic_docs[topic] += 1

        for word in words:
            vocabulary.add(word)
            word_topic[topic][word] += 1

        topic_answers[topic][item["answer"]] += 1

    model = {
        "examples_count": len(examples),
        "vocabulary": sorted(vocabulary),
        "topic_docs": dict(topic_docs),
        "word_topic": {
            topic: dict(counts)
            for topic, counts in word_topic.items()
        },
        "topic_answers": {
            topic: dict(counts)
            for topic, counts in topic_answers.items()
        }
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with MODEL_FILE.open("w", encoding="utf-8") as file:
        json.dump(model, file, ensure_ascii=False, indent=2)

    print("NEXA training complete!")
    print("Training examples:", len(examples))
    print("Vocabulary size:", len(vocabulary))
    print("Topics learned:", ", ".join(topic_docs.keys()))
    print("Model saved to:", MODEL_FILE)


if __name__ == "__main__":
    train()

