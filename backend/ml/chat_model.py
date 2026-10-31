
import json
import re
from pathlib import Path
from difflib import SequenceMatcher

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

MODEL_FILE = DATA_DIR / "nexa_model.json"
TRAIN_FILE = DATA_DIR / "train.jsonl"


def tokenize(text):
    return set(re.findall(r"[a-z0-9@']+", text.lower()))


def load_jsonl(path):
    examples = []

    if not path.exists():
        return examples

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                try:
                    examples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return examples


def load_model():
    if not MODEL_FILE.exists():
        return None

    try:
        with MODEL_FILE.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return None


def find_answer(message):
    model = load_model()
    examples = load_jsonl(TRAIN_FILE)

    if not examples:
        print("Training data nahi mila. data/train.jsonl check karo.")
        return None

    question_words = tokenize(message)
    if not question_words:
        return None

    best_answer = None
    best_score = 0.0

    for item in examples:
        question = item.get("question", "")
        answer = item.get("answer", "")

        if not question or not answer:
            continue

        example_words = tokenize(question)
        overlap = len(question_words & example_words)

        precision = overlap / len(question_words)
        recall = overlap / len(example_words) if example_words else 0
        jaccard = (
            overlap / len(question_words | example_words)
            if question_words | example_words else 0
        )
        similarity = SequenceMatcher(
            None, message.lower(), question.lower()
        ).ratio()

        score = max(
            jaccard,
            0.6 * precision + 0.4 * recall,
            similarity * 0.7
        )

        if score > best_score:
            best_score = score
            best_answer = answer

    if best_score < 0.25:
        return None

    return best_answer


def chat():
    print("NEXA: Namaste! Main NEXA hoon.")
    print("Band karne ke liye 'quit' likho.\n")

    while True:
        message = input("You: ").strip()

        if message.lower() in {"quit", "exit", "bye"}:
            print("NEXA: Bye!")
            break

        if not message:
            print("NEXA: Apna sawal likhiye.")
            continue

        answer = find_answer(message)

        if answer:
            print("NEXA:", answer)
        else:
            print("NEXA: Is sawal ka sahi jawab mere dataset mein abhi nahi hai.")


if __name__ == "__main__":
    chat()

