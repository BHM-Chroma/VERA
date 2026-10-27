"""Распознавание речи.

mode в config.json:
  "online"  — Google Speech (как в vera_4.py), нужен интернет
  "offline" — Vosk, нужна скачанная модель (путь vosk_model_path)
Микрофон слушается в фоне, распознанный текст уходит в on_text(text).
"""
import json
import os
import threading


class VoiceListener:
    def __init__(self, cfg, on_text, on_status=print):
        self.cfg = cfg["voice"]
        self.on_text = on_text
        self.on_status = on_status
        self._stop = None
        self._vosk_model = None
        self._lock = threading.Lock()

    def start(self):
        try:
            import speech_recognition as sr
        except ImportError:
            self.on_status("Голос выключен: не установлен SpeechRecognition.")
            return False

        if self.cfg["mode"] == "offline" and not self._load_vosk():
            return False

        self.r = sr.Recognizer()
        try:
            self.mic = sr.Microphone()
            with self.mic as source:
                self.r.adjust_for_ambient_noise(source, duration=1.5)
        except (OSError, AttributeError) as e:
            self.on_status(f"Голос выключен: микрофон недоступен ({e}).")
            return False

        self._stop = self.r.listen_in_background(
            self.mic, self._callback, phrase_time_limit=self.cfg["phrase_time_limit"])
        self.on_status(f"Слушаю микрофон ({self.cfg['mode']}).")
        return True

    def stop(self):
        if self._stop:
            self._stop(wait_for_stop=False)

    def _load_vosk(self):
        try:
            from vosk import Model, SetLogLevel
        except ImportError:
            self.on_status("Голос выключен: не установлен vosk.")
            return False
        path = self.cfg["vosk_model_path"]
        if not os.path.isabs(path):
            from paths import BASE_DIR
            path = os.path.join(BASE_DIR, path)
        if not os.path.isdir(path):
            self.on_status(f"Голос выключен: нет модели Vosk в {path}.")
            return False
        SetLogLevel(-1)
        self._vosk_model = Model(path)
        return True

    def _callback(self, recognizer, audio):
        import speech_recognition as sr
        text = ""
        try:
            if self.cfg["mode"] == "offline":
                text = self._recognize_vosk(audio)
            else:
                text = recognizer.recognize_google(audio, language="ru-RU")
        except sr.UnknownValueError:
            return
        except sr.RequestError:
            self.on_status("Google Speech недоступен (нет интернета?).")
            return
        text = (text or "").strip().lower()
        if text:
            self.on_text(text)

    def _recognize_vosk(self, audio):
        from vosk import KaldiRecognizer
        with self._lock:
            rec = KaldiRecognizer(self._vosk_model, 16000)
            rec.AcceptWaveform(audio.get_raw_data(convert_rate=16000, convert_width=2))
            return json.loads(rec.FinalResult()).get("text", "")
