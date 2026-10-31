"""Свои команды: «Запомни команду X, её действие Y».

Хранятся в user_commands.json рядом с мозгом (обновление их не трогает).
Действие — одна или несколько фраз через «и», «потом», «затем» или запятую:
    «Запомни команду игровой режим, её действие запусти стим и спрячься»
Каждая фраза выполняется так, как если бы ты сказал её сам.
"""
import json
import os
import re

from rapidfuzz import fuzz

from paths import path

FILE = path("user_commands.json")

# «запомни команду X, её действие Y» (и варианты без запятых — как их пишет распознавание речи)
TEACH_RE = re.compile(
    r"запомни(?:ть)?\s+(?:новую\s+|мою\s+)?(?:команду|фразу)\s+(?P<trig>.+?)"
    r"(?:\s*[,.;]?\s*(?:а\s+)?(?:её|ее|eё|ей|и)?\s*(?:действи\w*|делай|выполняй|означает|значит)\s*[:—–=-]?\s*"
    r"|\s*(?:—|–|->|=>|=|:)\s*)"
    r"(?P<act>.+)$")
TEACH_ONLY_RE = re.compile(r"запомни(?:ть)?\s+(?:новую\s+|мою\s+)?(?:команду|фразу)\s*(?P<trig>.*)$")
FORGET_RE = re.compile(r"(?:забудь|удали|сотри)\s+(?:мою\s+)?(?:команду|фразу)\s+(?P<trig>.+)$")
SPLIT_RE = re.compile(r"\s*(?:,|;|\s+а\s+потом\s+|\s+и\s+потом\s+|\s+и\s+затем\s+|\s+потом\s+|\s+затем\s+|\s+и\s+)\s*")
URL_RE = re.compile(r"(https?://\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|ru|kz|net|org|io|tv|me|app|gg|dev)\b\S*)")
SAY_RE = re.compile(r"^(?:скажи|ответь|говори|произнеси)\s+(?P<text>.+)$")
QUOTES = "«»\"'“”„"


def strip_quotes(s):
    return s.strip().strip(QUOTES + " ,.").strip()


def split_action(action):
    """«открой телеграм и спотифай, потом спрячься» -> ['открой телеграм', 'спотифай', 'спрячься']."""
    parts = []
    for p in SPLIT_RE.split(action):
        words = strip_quotes(p).split()
        while words and words[0] in ("и", "а", "потом", "затем", "ещё", "еще", "после"):
            words.pop(0)
        if words:
            parts.append(" ".join(words))
    return parts


def url_of(part):
    m = URL_RE.search(part)
    if not m:
        return None
    url = m.group(1)
    return url if url.startswith("http") else "https://" + url


def say_text(part):
    m = SAY_RE.match(part)
    return m.group("text").strip() if m else None


class UserCommands:
    def __init__(self):
        self.items = []          # [{"trigger": "игровой режим", "steps": ["запусти стим", "спрячься"]}]
        self.load()

    def load(self):
        if os.path.exists(FILE):
            try:
                with open(FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.items = [d for d in data if d.get("trigger") and d.get("steps")]
            except (OSError, json.JSONDecodeError):
                pass

    def save(self):
        with open(FILE, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)

    def add(self, trigger, steps):
        """Возвращает True, если команда уже была и её перезаписали."""
        old = self.find(trigger, threshold=95)
        if old:
            old["steps"] = steps
            old["trigger"] = trigger
            self.save()
            return True
        self.items.append({"trigger": trigger, "steps": steps})
        self.save()
        return False

    def remove(self, trigger):
        found = self.find(trigger, threshold=75)
        if found:
            self.items.remove(found)
            self.save()
        return found

    def clear(self):
        n = len(self.items)
        self.items = []
        self.save()
        return n

    def find(self, text, threshold=88):
        best, best_s = None, 0
        for item in self.items:
            s = fuzz.ratio(text, item["trigger"])
            if s > best_s:
                best, best_s = item, s
        return best if best_s >= threshold else None
