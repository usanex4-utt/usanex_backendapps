import json
from pathlib import Path
from mood_model import MoodClassifier

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "mood_dataset.jsonl"

def train():
    if not DATA_FILE.exists():
        print(f"❌ Dataset nahi mila: {DATA_FILE}. Pehle data create karein.")
        return
    
    examples = []
    with DATA_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    examples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    classifier = MoodClassifier()
    classifier.train(examples)
    print(f"✅ AI Mood Model trained on {len(examples)} examples!")
    print(f"   Saved to: {classifier.model_path}")

if __name__ == "__main__":
    train()