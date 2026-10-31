
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

FILES_TO_CHECK = [
    DATA_DIR / "nexa_dataset.jsonl",
    DATA_DIR / "usanex_knowledge.jsonl",
]


def validate_file(path):
    if not path.exists():
        print(f"FILE MISSING: {path.name}")
        return

    total = 0
    invalid = 0
    missing_fields = 0

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                print(f"Invalid JSON: {path.name}, line {line_number}")
                continue

            if not isinstance(item, dict):
                invalid += 1
                print(f"Expected JSON object: {path.name}, line {line_number}")
                continue

            total += 1

            if not str(item.get("question", "")).strip() or not str(
                item.get("answer", "")
            ).strip():
                missing_fields += 1
                print(f"Question/answer missing: {path.name}, line {line_number}")

    print(f"\nFile: {path.name}")
    print(f"Valid JSON records: {total}")
    print(f"Invalid JSON lines: {invalid}")
    print(f"Records missing question/answer: {missing_fields}")


if __name__ == "__main__":
    for path in FILES_TO_CHECK:
        validate_file(path)
