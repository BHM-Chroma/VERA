"""История диалога: хранится в memory.json рядом со скриптом."""
import json
import os

from paths import path

MEMORY_FILE = path("memory.json")


def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except (OSError, json.JSONDecodeError):
            pass
    return []


def save_memory(history, keep=200):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-keep:], f, ensure_ascii=False, indent=2)
