import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_FILE = DATA_DIR / "nexa_dataset.jsonl"


def add_example(question, answer, topic="general"):
    question = question.strip()
    answer = answer.strip()

    if not question or not answer:
        print("Question aur answer dono zaroori hain.")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    record = {
        "question": question,
        "answer": answer,
        "topic": topic.strip() or "general"
    }

    with DATA_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")

    print("Example dataset mein add ho gaya.")


if __name__ == "__main__":
    question = input("Question: ")
    answer = input("Answer: ")
    topic = input("Topic (optional): ")

    add_example(question, answer, topic)