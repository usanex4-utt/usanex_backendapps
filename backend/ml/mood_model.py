import json
import re
import math
from pathlib import Path
from collections import Counter, defaultdict

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MODEL_FILE = DATA_DIR / "nexa_mood_model.json"


def tokenize(text):
    return re.findall(r"[a-z0-9@']+", text.lower())


class MoodClassifier:
    def __init__(self, model_path=MODEL_FILE):
        self.model_path = model_path
        self.word_counts = defaultdict(Counter)
        self.mood_counts = Counter()
        self.total_moods = 0
        self.vocab = set()
        self.load()

    def load(self):
        if self.model_path.exists():
            try:
                with self.model_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                    # Fix: Convert normal dicts back to Counter objects
                    self.word_counts = defaultdict(Counter)
                    for mood, counts in data.get("word_counts", {}).items():
                        self.word_counts[mood] = Counter(counts)
                        
                    self.mood_counts = Counter(data.get("mood_counts", {}))
                    self.total_moods = data.get("total_moods", 0)
                    self.vocab = set(data.get("vocab", []))
            except Exception as e:
                print(f"Error loading mood model: {e}")

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "word_counts": {mood: dict(counts) for mood, counts in self.word_counts.items()},
            "mood_counts": dict(self.mood_counts),
            "total_moods": self.total_moods,
            "vocab": sorted(self.vocab),
        }
        with self.model_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def train(self, examples):
        for ex in examples:
            text = ex.get("text", "")
            mood = ex.get("mood", "calm")
            words = tokenize(text)
            if not words:
                continue
            self.mood_counts[mood] += 1
            self.total_moods += 1
            for w in words:
                self.word_counts[mood][w] += 1
                self.vocab.add(w)
        self.save()

    def predict(self, text):
        if not text or self.total_moods == 0:
            return None
        words = tokenize(text)
        if not words:
            return None

        scores = {}
        vocab_size = len(self.vocab) or 1

        for mood in self.mood_counts:
            log_prob = math.log(self.mood_counts[mood] / self.total_moods)
            for w in words:
                word_count = self.word_counts[mood].get(w, 0)
                total_words_in_mood = sum(self.word_counts[mood].values())
                prob = (word_count + 1) / (total_words_in_mood + vocab_size)
                log_prob += math.log(prob)
            scores[mood] = log_prob

        if not scores:
            return None
        best_mood = max(scores, key=scores.get)
        return best_mood

    def predict_with_confidence(self, text):
        """
        Returns (mood_key, confidence) with confidence in [0, 1] (softmax).
        """
        if not text or self.total_moods == 0:
            return None, 0.0

        words = tokenize(text)
        if not words:
            return None, 0.0

        scores = {}
        vocab_size = len(self.vocab) or 1

        for mood in self.mood_counts:
            log_prob = math.log(self.mood_counts[mood] / self.total_moods)
            total_words_in_mood = sum(self.word_counts[mood].values())
            for w in words:
                word_count = self.word_counts[mood].get(w, 0)
                prob = (word_count + 1) / (total_words_in_mood + vocab_size)  # Laplace
                log_prob += math.log(prob)
            scores[mood] = log_prob

        if not scores:
            return None, 0.0

        # Softmax over log-probs
        max_log = max(scores.values())
        exps = {k: math.exp(v - max_log) for k, v in scores.items()}
        z = sum(exps.values()) or 1.0
        probs = {k: v / z for k, v in exps.items()}

        best = max(probs, key=probs.get)
        return best, probs[best]