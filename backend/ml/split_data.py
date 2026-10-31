
import json
import random
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

SOURCE_FILE = DATA_DIR / "nexa_dataset.jsonl"
TRAIN_FILE = DATA_DIR / "train.jsonl"
VALIDATION_FILE = DATA_DIR / "validation.jsonl"
TEST_FILE = DATA_DIR / "test.jsonl"

RANDOM_SEED = 42


def split_dataset():
    if not SOURCE_FILE.exists():
        print(f"Dataset nahi mila: {SOURCE_FILE}")
        return

    examples = []

    with SOURCE_FILE.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            try:
                item = json.loads(line)
                if item.get("question") and item.get("answer"):
                    examples.append(item)
                else:
                    print(f"Question/answer missing: line {line_number}")
            except json.JSONDecodeError:
                print(f"Invalid JSON: line {line_number}")

    if not examples:
        print("Dataset mein valid examples nahi hain.")
        return

    random.Random(RANDOM_SEED).shuffle(examples)

    total = len(examples)

    if total < 3:
        print("Train, validation aur test split ke liye kam se kam 3 examples chahiye.")
        return

    train_end = max(1, int(total * 0.70))
    validation_end = max(train_end + 1, int(total * 0.85))
    validation_end = min(validation_end, total - 1)

    train = examples[:train_end]
    validation = examples[train_end:validation_end]
    test = examples[validation_end:]

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for path, records in [
        (TRAIN_FILE, train),
        (VALIDATION_FILE, validation),
        (TEST_FILE, test),
    ]:
        with path.open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Total examples: {total}")
    print(f"Train: {len(train)}")
    print(f"Validation: {len(validation)}")
    print(f"Test: {len(test)}")


if __name__ == "__main__":
    split_dataset()
