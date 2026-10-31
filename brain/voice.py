"""Распознавание речи.

mode в config.json:
  "online"  — Google Speech (как в vera_4.py), нужен интернет
  "offline" — Vosk, нужна скачанная модель (путь vosk_model_path)
Микрофон слушается в фоне, распознанные варианты фразы уходят в on_text([вариант1, вариант2, …]) —
мозг сам выбирает тот, что больше похож на команду или содержит её имя.

Настройки (config.json → "voice"):
  pause_sec         — сколько тишины считать концом фразы (меньше — отвечает быстрее, но может перебить)
  phrase_time_limit — самая длинная фраза в секундах
  device_name       — часть названия микрофона, если их несколько ("" — микрофон по умолчанию)
Чувствительность и «не слушать» меняются из меню персонажа и запоминаются в voice_state.json.
"""
import json
import os
import threading

from paths import path

STATE_FILE = path("voice_state.json")
# чувствительность: во сколько раз порог громкости выше фонового шума (больше — нужно говорить громче)
SENSITIVITY = {0: ("низкая", 2.2), 1: ("обычная", 1.5), 2: ("высокая", 1.15)}


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"muted": bool(data.get("muted", False)), "sensitivity": int(data.get("sensitivity", 1))}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"muted": False, "sensitivity": 1}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass


class VoiceListener:
    def __init__(self, cfg, on_text, on_status=print):
        self.cfg = cfg["voice"]
        self.on_text = on_text
        self.on_status = on_status
        self._stop = None
        self._vosk_model = None
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.state = load_state()
        self.ready = False
        self._base_energy = 300.0

    # ---------- запуск ----------

    def start(self):
        try:
            import speech_recognition as sr
        except ImportError:
            self.on_status("Голос выключен: не установлен SpeechRecognition.")
            return False

        if self.cfg["mode"] == "offline" and not self._load_vosk():
            return False

        self.r = sr.Recognizer()
        pause = float(self.cfg.get("pause_sec", 0.6))
        self.r.pause_threshold = pause
        self.r.non_speaking_duration = min(0.4, pause)
        self.r.dynamic_energy_threshold = True
        try:
            self.mic = sr.Microphone(device_index=self._device_index(sr))
            self.calibrate()
        except (OSError, AttributeError) as e:
            self.on_status(f"Голос выключен: микрофон недоступен ({e}).")
            return False
        self.ready = True
        if self.state["muted"]:
            self.on_status("Микрофон выключен (из меню) — не слушаю.")
        else:
            self._listen()
        return True

    def _device_index(self, sr):
        try:
            names = sr.Microphone.list_microphone_names()
        except (OSError, AttributeError):
            return None
        want = str(self.cfg.get("device_name", "") or "").lower()
        if names:
            self.on_status("Микрофоны: " + "; ".join(f"{i}: {n}" for i, n in enumerate(names[:12])))
        if want:
            for i, n in enumerate(names):
                if want in n.lower():
                    self.on_status(f"Выбран микрофон {i}: {n}")
                    return i
            self.on_status(f"Микрофон «{want}» не найден — беру по умолчанию.")
        return None

    def _listen(self):
        if self._stop or not self.ready:
            return
        self._stop = self.r.listen_in_background(
            self.mic, self._callback, phrase_time_limit=self.cfg["phrase_time_limit"])
        self.on_status(f"Слушаю микрофон ({self.cfg['mode']}, чувствительность "
                       f"{SENSITIVITY[self.state['sensitivity']][0]}).")

    def stop(self, wait=True):
        if self._stop:
            self._stop(wait_for_stop=wait)   # wait — дождаться, тогда микрофон точно отпущен
            self._stop = None

    # ---------- управление из меню ----------

    def set_muted(self, muted):
        with self._state_lock:
            self.state["muted"] = bool(muted)
            save_state(self.state)
            if not self.ready:
                return
            if muted:
                self.stop()
                self.on_status("Микрофон выключен — не слушаю.")
            else:
                self._listen()

    def set_sensitivity(self, level):
        level = level if level in SENSITIVITY else 1
        with self._state_lock:
            self.state["sensitivity"] = level
            save_state(self.state)
            if self.ready:
                self._apply_sensitivity()
        self.on_status(f"Чувствительность: {SENSITIVITY[level][0]}.")

    def calibrate(self):
        """Послушать тишину и подстроить порог громкости под шум в комнате."""
        was_listening = self._stop is not None
        if was_listening:
            self.stop()
        with self.mic as source:
            self.r.adjust_for_ambient_noise(source, duration=1.5)
        self._base_energy = max(50.0, self.r.energy_threshold / 1.5)   # примерно уровень фона
        self._apply_sensitivity()
        self.on_status(f"Шум подстроен: порог {int(self.r.energy_threshold)}.")
        if was_listening:
            self._listen()

    def _apply_sensitivity(self):
        ratio = SENSITIVITY[self.state["sensitivity"]][1]
        self.r.dynamic_energy_ratio = ratio
        self.r.energy_threshold = self._base_energy * ratio

    # ---------- распознавание ----------

    def _load_vosk(self):
        try:
            from vosk import Model, SetLogLevel
        except ImportError:
            self.on_status("Голос выключен: не установлен vosk.")
            return False
        p = self.cfg["vosk_model_path"]
        if not os.path.isabs(p):
            from paths import BASE_DIR
            p = os.path.join(BASE_DIR, p)
        if not os.path.isdir(p):
            self.on_status(f"Голос выключен: нет модели Vosk в {p}.")
            return False
        SetLogLevel(-1)
        self._vosk_model = Model(p)
        return True

    def _callback(self, recognizer, audio):
        import speech_recognition as sr
        if self.state["muted"]:
            return
        try:
            if self.cfg["mode"] == "offline":
                variants = [self._recognize_vosk(audio)]
            else:
                # несколько вариантов услышанного: мозг выберет тот, что похож на команду
                res = recognizer.recognize_google(audio, language="ru-RU", show_all=True)
                alts = res.get("alternative", []) if isinstance(res, dict) else []
                variants = [a.get("transcript", "") for a in alts]
        except sr.UnknownValueError:
            return
        except sr.RequestError:
            self.on_status("Google Speech недоступен (нет интернета?).")
            return
        variants = [v.strip().lower() for v in variants if v and v.strip()]
        if variants and not self.state["muted"]:
            self.on_text(list(dict.fromkeys(variants)))

    def _recognize_vosk(self, audio):
        from vosk import KaldiRecognizer
        with self._lock:
            rec = KaldiRecognizer(self._vosk_model, 16000)
            rec.AcceptWaveform(audio.get_raw_data(convert_rate=16000, convert_width=2))
            return json.loads(rec.FinalResult()).get("text", "")
