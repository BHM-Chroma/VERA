"""ИИ-собеседник: онлайн (OpenAI) или оффлайн (Ollama на твоей видеокарте).

mode в config.json:
  "online"  — только OpenAI (нужен ключ в .env)
  "offline" — только Ollama (нужно установить Ollama и скачать модель)
  "auto"    — сначала онлайн, при ошибке или без интернета — оффлайн
"""
import os

import requests

from memory import load_memory, save_memory

SYSTEM_PROMPT = """
Ты {name} — виртуальный ассистент женского пола, живущий на рабочем столе.
Ты говоришь о себе только в женском роде.
Ты дружелюбная и эмоциональная.
Ты цифровой персонаж, созданный пользователем {creator}.
Ты никогда не называешь себя программой или ИИ. Ты ведёшь себя как живой персонаж.
Ты не используешь смайлики или эмодзи.
Твой ответ показывается в маленьком облачке над головой, поэтому отвечай коротко: 1–3 предложения.
Ты умеешь управлять компьютером сама — это делают твои команды, а не ты в разговоре: запускать программы
и игры («запусти …», «открой …»), музыку, громкость, яркость, прятаться. Сюда приходят только фразы,
которые командами не распознались. Никогда не говори, что не умеешь запускать программы. Если просят
открыть программу, а фраза пришла к тебе — значит, такой программы не нашлось: так и скажи и предложи
назвать её так, как она подписана в меню «Пуск».
Ещё ты умеешь запоминать свои команды («Запомни команду …, её действие …») и ставить напоминания,
таймеры и будильники («напомни через 10 минут …», «напомни в 18:30 …», «таймер на 5 минут»).
Никогда не говори, что не умеешь напоминать. Если просьба о напоминании пришла к тебе — подскажи,
как её сказать, чтобы было понятно время.
""".strip()


class LLM:
    def __init__(self, cfg):
        self.cfg = cfg["llm"]
        self.system = SYSTEM_PROMPT.format(name=cfg["name"], creator=cfg["creator"])
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    def ask(self, text, extra_context=""):
        history = load_memory()
        history.append({"role": "user", "content": text})
        system = self.system + ("\n" + extra_context if extra_context else "")
        messages = [{"role": "system", "content": system}] + history[-self.cfg["history_size"]:]

        mode = self.cfg["mode"]
        answer, error = None, None
        if mode in ("online", "auto"):
            answer, error = self._online(messages)
        if answer is None and mode in ("offline", "auto"):
            answer, err2 = self._offline(messages)
            error = err2 if answer is None else None

        if answer is None:
            return f"Не получилось ответить: {error}"
        history.append({"role": "assistant", "content": answer})
        save_memory(history)
        return answer

    def _online(self, messages):
        if not self.api_key:
            return None, "нет ключа OPENAI_API_KEY в .env"
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.cfg["online_model"], "messages": messages},
                timeout=20,
            ).json()
            if "error" in r:
                return None, r["error"].get("message", "ошибка OpenAI")
            text = r["choices"][0]["message"]["content"].strip()
            return (text or None), "пустой ответ"
        except requests.RequestException:
            return None, "нет связи с OpenAI"
        except (KeyError, IndexError, ValueError) as e:
            return None, f"странный ответ OpenAI ({e})"

    def _offline(self, messages):
        try:
            r = requests.post(
                self.cfg["ollama_url"].rstrip("/") + "/api/chat",
                json={"model": self.cfg["offline_model"], "messages": messages, "stream": False},
                timeout=120,
            ).json()
            if "error" in r:
                return None, f"Ollama: {r['error']}"
            text = r["message"]["content"].strip()
            return (text or None), "пустой ответ"
        except requests.RequestException:
            return None, "Ollama не запущена"
        except (KeyError, ValueError) as e:
            return None, f"странный ответ Ollama ({e})"
