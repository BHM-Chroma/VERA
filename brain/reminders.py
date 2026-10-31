"""Напоминания, таймеры и будильники.

«напомни через 20 минут выключить духовку», «напомни в 18:30 позвонить маме»,
«напомни завтра в 9 утра про встречу», «таймер на 5 минут», «засеки полчаса»,
«будильник на 7:30». Хранятся в reminders.json — переживают перезапуск.
"""
import datetime
import json
import os
import re
import time
import uuid

from paths import path

FILE = path("reminders.json")

UNITS = (("сек", 1), ("мин", 60), ("час", 3600), ("сут", 86400), ("дн", 86400), ("ден", 86400))
ONES = {"ноль": 0, "один": 1, "одну": 1, "одна": 1, "одной": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
        "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
        "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
        "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19, "пару": 2, "пара": 2}
TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50}
DAYPARTS = {"утра": "am", "утром": "am", "дня": "pm", "днём": "pm", "днем": "pm", "вечера": "pm",
            "вечером": "pm", "ночи": "night", "ночью": "night"}
TIMER_WORDS = ("таймер", "засеки", "засечь")
ALARM_WORDS = ("будильник", "разбуди")
REMIND_WORDS = ("напомни", "напомнить", "напоминание")
# слова, которые остаются от команды и не нужны в тексте напоминания
JUNK = {"напомни", "напомнить", "напоминание", "поставь", "поставить", "установи", "заведи", "создай",
        "таймер", "засеки", "засечь", "будильник", "разбуди", "мне", "меня", "нам", "пожалуйста", "что",
        "чтобы", "чтоб", "о", "об", "про", "том", "на", "в", "через", "и", "сделай", "мой"}

TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")
NUM_RE = re.compile(r"^\d+(?:[.,]\d+)?$")


def _unit(word):
    for stem, sec in UNITS:
        if word.startswith(stem):
            return sec
    return None


def _number(words, i):
    """Число в words[i:]: «5», «1,5», «двадцать пять», «полтора». Возвращает (число, сколько слов) или None."""
    if i >= len(words):
        return None
    w = words[i]
    if NUM_RE.match(w):
        return float(w.replace(",", ".")), 1
    if w.startswith("полтор"):
        return 1.5, 1
    if w in TENS:
        if i + 1 < len(words) and words[i + 1] in ONES and ONES[words[i + 1]] < 10:
            return TENS[w] + ONES[words[i + 1]], 2
        return TENS[w], 1
    if w in ONES:
        return ONES[w], 1
    return None


def _duration(words, i):
    """Длительность с позиции i: «5 минут», «час и 15 минут», «полчаса», «полторы минуты».
    Возвращает (секунды, индекс после) или None."""
    total, j, found = 0.0, i, False
    while j < len(words):
        w = words[j]
        if found and w == "и":
            j += 1
            continue
        if w.startswith("пол") and _unit(w[3:]) and not w.startswith("полтор"):   # полчаса, полминуты
            total += 0.5 * _unit(w[3:])
            j, found = j + 1, True
            continue
        num = _number(words, j)
        if num and j + num[1] < len(words) and _unit(words[j + num[1]]):
            total += num[0] * _unit(words[j + num[1]])
            j, found = j + num[1] + 1, True
            continue
        if _unit(w) and not num:                                                   # «через час»
            total += _unit(w)
            j, found = j + 1, True
            continue
        break
    return (total, j) if found and total > 0 else None


def _clock(words, i):
    """Время суток с позиции i: «18:30», «18 30», «7 вечера», «9 утра», «полдень».
    Возвращает (час, минута, индекс после, указано ли утро/вечер) или None."""
    if i >= len(words):
        return None
    w = words[i]
    if w == "полдень":
        return 12, 0, i + 1, True
    if w == "полночь":
        return 0, 0, i + 1, True
    m = TIME_RE.match(w)
    if m:
        h, mi, j = int(m.group(1)), int(m.group(2)), i + 1
    else:
        num = _number(words, i)
        if not num or num[0] != int(num[0]):
            return None
        h, j, mi = int(num[0]), i + num[1], 0
        if j < len(words) and words[j].startswith("час"):   # «в 23 часа»
            j += 1
        elif j < len(words) and _unit(words[j]):     # «в 5 минут» — это не время суток
            return None
        if j < len(words) and re.fullmatch(r"\d{2}", words[j]):   # «18 30»
            mi, j = int(words[j]), j + 1
            if j < len(words) and words[j].startswith("мин"):
                j += 1
    if j < len(words) and words[j] in DAYPARTS:
        part = DAYPARTS[words[j]]
        if part == "pm" and h < 12:
            h += 12
        elif part == "night" and h == 12:
            h = 0
        elif part == "am" and h == 12:
            h = 0
        j += 1
        if h > 23 or mi > 59:
            return None
        return h, mi, j, True
    if h > 23 or mi > 59:
        return None
    return h, mi, j, False


def is_request(text):
    return any(w in text for w in REMIND_WORDS + TIMER_WORDS + ALARM_WORDS)


def parse(text, now=None):
    """Разбирает просьбу. Возвращает dict(when, text, kind, label) или None, если время не понятно."""
    now = now or time.time()
    words = [w.strip(",!?;«»\"") for w in text.lower().replace("ё", "е").split()]
    words = [w for w in words if w]
    kind = "timer" if any(w in TIMER_WORDS for w in words) else \
        "alarm" if any(w in ALARM_WORDS for w in words) else "reminder"
    used = set()
    when, label = None, ""

    # относительное время: «через …», «таймер на …», «засеки …»
    for i, w in enumerate(words):
        start = None
        if w == "через":
            start = i + 1
        elif kind == "timer" and (w in TIMER_WORDS or w == "на"):
            start = i + 1
        if start is None:
            continue
        d = _duration(words, start)
        if d:
            secs, end = d
            when = now + secs
            label = " ".join(words[start:end])
            used.update(range(i, end))
            break

    # время суток: «в 18:30», «на 7 утра», «завтра в 9»
    if when is None:
        day_shift = 0
        for i, w in enumerate(words):
            if w in ("завтра", "послезавтра", "сегодня"):
                day_shift = {"завтра": 1, "послезавтра": 2}.get(w, 0)
                used.add(i)
        for i, w in enumerate(words):
            starts = [i + 1] if w in ("в", "во", "на", "к") else ([i] if TIME_RE.match(w) or w in ("полдень", "полночь") else [])
            for s in starts:
                c = _clock(words, s)
                if not c:
                    continue
                h, mi, end, explicit = c
                base = datetime.datetime.fromtimestamp(now)
                target = base.replace(hour=h, minute=mi, second=0, microsecond=0) + datetime.timedelta(days=day_shift)
                if day_shift == 0 and target.timestamp() <= now:
                    if not explicit and kind != "alarm" and 1 <= h <= 11 and (target + datetime.timedelta(hours=12)).timestamp() > now:
                        target += datetime.timedelta(hours=12)   # «в 5» днём — скорее всего 17:00
                    else:
                        target += datetime.timedelta(days=1)
                when = target.timestamp()
                label = target.strftime("%H:%M")
                if day_shift or target.date() != base.date():
                    label = ("завтра в " if (target.date() - base.date()).days == 1 else target.strftime("%d.%m в ")) + label
                else:
                    label = "в " + label
                used.update(range(i if s == i + 1 else s, end))
                break
            if when is not None:
                break

    if when is None:
        return None
    rest = [w for i, w in enumerate(words) if i not in used]
    prep = ""
    while rest and (rest[0] in JUNK or rest[0] in ("вероника", "вера")):
        w = rest.pop(0)
        if w in ("про", "о", "об"):
            prep = w
        elif w not in ("том",):
            prep = ""
    while rest and rest[-1] in JUNK:
        rest.pop()
    message = " ".join(rest).strip(" ,.")
    if prep and message.split()[0] in ("том",):
        prep = ""
    return {"id": uuid.uuid4().hex[:8], "when": when, "text": message, "kind": kind, "prep": prep,
            "label": label, "created": now}


def left_words(seconds):
    s = max(0, int(round(seconds)))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    parts = []
    if h:
        parts.append(f"{h} {_plural(h, 'час', 'часа', 'часов')}")
    if m:
        parts.append(f"{m} {_plural(m, 'минута', 'минуты', 'минут')}")
    if sec and not h and (m < 5):
        parts.append(f"{sec} {_plural(sec, 'секунда', 'секунды', 'секунд')}")
    return " ".join(parts) or "меньше секунды"


def _plural(n, one, few, many):
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def when_words(r, now=None):
    """«через 20 минут», «в 18:30», «завтра в 09:00»."""
    now = now or time.time()
    left = r["when"] - now
    if left < 3600 * 3:
        return "через " + left_words(left)
    dt = datetime.datetime.fromtimestamp(r["when"])
    days = (dt.date() - datetime.datetime.fromtimestamp(now).date()).days
    return ("в " if days == 0 else "завтра в " if days == 1 else dt.strftime("%d.%m в ")) + dt.strftime("%H:%M")


def what_words(r):
    if r["text"]:
        return (r.get("prep", "") + " " + r["text"]).strip()
    return {"timer": "таймер", "alarm": "будильник"}.get(r["kind"], "напоминание")


def describe(r, now=None):
    """Коротко: «через 15 минут — позвонить маме»."""
    return f"{when_words(r, now)} — {what_words(r)}"


def confirm_text(r, now=None):
    if r["kind"] == "timer":
        return f"Засекла {r['label']}." + (f" Потом напомню: {r['text']}." if r["text"] else "")
    if r["kind"] == "alarm":
        return f"Будильник поставлен — {when_words(r, now)}."
    if r["text"]:
        return f"Хорошо, напомню {when_words(r, now)}{' ' + r['prep'] if r.get('prep') else ':'} {r['text']}."
    return f"Хорошо, напомню {when_words(r, now)}."


def fire_text(r):
    if r["kind"] == "timer":
        return f"Таймер{' на ' + r['label'] if r['label'] else ''} вышел!" + (f" {r['text'].capitalize()}." if r["text"] else "")
    if r["kind"] == "alarm":
        return "Подъём! Будильник!" + (f" {r['text'].capitalize()}." if r["text"] else "")
    if r["text"] and r.get("prep"):
        return f"Напоминаю {r['prep']} {r['text']}!"
    return f"Напоминаю: {r['text']}!" if r["text"] else "Ты просил напомнить — время пришло!"


class Reminders:
    def __init__(self):
        self.items = []
        self.load()

    def load(self):
        if os.path.exists(FILE):
            try:
                with open(FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.items = [d for d in data if "when" in d]
            except (OSError, json.JSONDecodeError):
                pass

    def save(self):
        with open(FILE, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)

    def add(self, r):
        self.items.append(r)
        self.items.sort(key=lambda x: x["when"])
        self.save()

    def due(self, now=None):
        now = now or time.time()
        ready = [r for r in self.items if r["when"] <= now]
        if ready:
            self.items = [r for r in self.items if r["when"] > now]
            self.save()
        return ready

    def upcoming(self):
        return sorted(self.items, key=lambda x: x["when"])

    def cancel(self, query="", kind=None):
        """Отменить: по словам из текста, иначе ближайшее нужного вида. Возвращает список отменённых."""
        pool = [r for r in self.items if kind is None or r["kind"] == kind]
        if not pool:
            return []
        picked = None
        if query:
            from rapidfuzz import fuzz
            scored = [(fuzz.partial_ratio(query, r["text"]), r) for r in pool if r["text"]]
            scored.sort(key=lambda x: -x[0])
            if scored and scored[0][0] >= 70:
                picked = scored[0][1]
        picked = picked or min(pool, key=lambda r: r["when"])
        self.items.remove(picked)
        self.save()
        return [picked]

    def clear(self):
        n = len(self.items)
        self.items = []
        self.save()
        return n
