"""Голос: микрофон → фразы → текст.

Как это устроено (переделано с нуля):
  1. Захват: микрофон читается непрерывно кусочками по 30 мс (16 кГц, моно) в своём потоке.
  2. Где речь: детектор речи WebRTC VAD (отличает голос от шума и музыки) + порог громкости
     над фоновым шумом, который подстраивается сам. Начало фразы — 0.15 с речи подряд,
     конец — end_silence секунд тишины (по умолчанию 0.8). К фразе добавляется 0.3 с «до»,
     чтобы не съедать первый слог.
  3. Клавиша «зажми и говори» (push-to-talk): пока зажата — пишется всё подряд, без детектора
     и без её имени; отпустил — фраза уходит сразу.
  4. Распознавание: Google (ru-RU), несколько вариантов; громкость фразы выравнивается перед
     отправкой. Распознаёт отдельный поток — микрофон в это время продолжает слушать.

События для персонажа: on_event("listening" | "thinking" | "idle").
Настройки (config.json → "voice"): end_silence, max_phrase_sec, device_name, mode.
Из меню (запоминается в voice_state.json): «не слушать», чувствительность, клавиша.
"""
import array
import collections
import math
import json
import os
import queue
import sys
import threading
import time

from paths import path

STATE_FILE = path("voice_state.json")
RATE = 16000
FRAME_MS = 30
FRAME = RATE * FRAME_MS // 1000          # 480 сэмплов
FRAME_BYTES = FRAME * 2

# чувствительность: (название, агрессивность VAD 0–3, во сколько раз громче фона должна быть речь)
SENSITIVITY = {0: ("низкая", 3, 3.0), 1: ("обычная", 2, 2.0), 2: ("высокая", 1, 1.4)}
# клавиши «зажми и говори» (виртуальные коды Windows)
PTT_KEYS = {
    "f8": (0x77, "F8"),
    "rctrl": (0xA3, "правый Ctrl"),
    "ralt": (0xA5, "правый Alt"),
    "xbutton1": (0x05, "боковая кнопка мыши (назад)"),
    "xbutton2": (0x06, "боковая кнопка мыши (вперёд)"),
    "none": (0, "нет"),
}


# ---------- звук без audioop (его убрали из новых версий Python) ----------

def _samples(raw):
    a = array.array("h")
    a.frombytes(raw[:len(raw) - len(raw) % 2])
    return a


def rms(raw):
    a = _samples(raw)
    return math.sqrt(sum(x * x for x in a) / len(a)) if a else 0.0


def peak(raw):
    a = _samples(raw)
    return max((abs(x) for x in a), default=0)


def scale(raw, gain):
    a = _samples(raw)
    return array.array("h", (max(-32768, min(32767, int(x * gain))) for x in a)).tobytes()


def resample(raw, src_rate):
    """Перевод в 16 кГц (линейная интерполяция). Только если микрофон не умеет 16 кГц сам."""
    a = _samples(raw)
    if src_rate == RATE or not a:
        return raw
    step = src_rate / RATE
    n = int(len(a) / step)
    out = array.array("h", [0]) * n
    for i in range(n):
        x = i * step
        j = int(x)
        f = x - j
        b = a[j + 1] if j + 1 < len(a) else a[j]
        out[i] = int(a[j] + (b - a[j]) * f)
    return out.tobytes()


def load_state():
    st = {"muted": False, "sensitivity": 1, "ptt_key": "f8"}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        st["muted"] = bool(data.get("muted", False))
        st["sensitivity"] = int(data.get("sensitivity", 1)) if int(data.get("sensitivity", 1)) in SENSITIVITY else 1
        st["ptt_key"] = data.get("ptt_key", "f8") if data.get("ptt_key", "f8") in PTT_KEYS else "f8"
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return st


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass


# ---------- где в звуке речь ----------

class Segmenter:
    """Режет поток 30-мс кусочков на фразы. Не знает ничего о микрофоне — его легко проверить."""

    def __init__(self, end_silence=0.8, max_phrase=12.0, sensitivity=1, use_vad=True):
        self.end_frames = max(8, int(end_silence * 1000 / FRAME_MS))
        self.max_frames = int(max_phrase * 1000 / FRAME_MS)
        self.pre = collections.deque(maxlen=10)            # 0.3 с до начала фразы
        self.recent = collections.deque(maxlen=5)          # для старта: 4 из 5 кусочков — речь
        self.noise = 120.0                                 # фоновый шум (RMS), подстраивается сам
        self.vad = None
        if use_vad:
            try:
                import webrtcvad
                self.vad = webrtcvad.Vad(2)
            except ImportError:
                self.vad = None
        self.set_sensitivity(sensitivity)
        self.reset()

    def set_sensitivity(self, level):
        _, aggr, gate = SENSITIVITY.get(level, SENSITIVITY[1])
        self.gate = gate
        if self.vad:
            self.vad.set_mode(aggr)

    def reset(self, keep_noise=True):
        self.active = False
        self.ptt = False
        self.frames = []
        self.voiced = 0
        self.silence = 0
        self.recent.clear()
        if not keep_noise:
            self.noise = 120.0

    def is_speech(self, frame, level):
        loud = level > max(60.0, self.noise * self.gate)
        if self.vad:
            try:
                return loud and self.vad.is_speech(frame, RATE)
            except Exception:  # noqa: BLE001
                return loud
        return loud

    def push(self, frame, ptt_down=False):
        """Кусочек звука. Возвращает ("start", None), ("end", (bytes, ptt)), ("drop", None) или (None, None)."""
        level = rms(frame)
        speech = self.is_speech(frame, level)
        if not speech and not self.active:
            # фон: медленно подстраиваемся (быстрее вниз, медленнее вверх)
            k = 0.05 if level < self.noise else 0.01
            self.noise = max(30.0, self.noise + (level - self.noise) * k)

        # клавиша «зажми и говори»
        if ptt_down:
            if not self.active:
                self.active, self.ptt = True, True
                self.frames = list(self.pre)
                self.voiced, self.silence = 0, 0
                self.frames.append(frame)
                return "start", None
            self.ptt = True
            self.frames.append(frame)
            self.voiced += speech
            self.silence = 0
            if len(self.frames) >= self.max_frames * 2:
                return self._finish()
            return None, None
        if self.ptt and self.active:
            # отпустили клавишу — ещё 0.15 с хвоста и отправляем
            self.frames.append(frame)
            self.silence += 1
            if self.silence >= 5:
                return self._finish()
            return None, None

        if not self.active:
            self.pre.append(frame)
            self.recent.append(speech)
            if sum(self.recent) >= 4:
                self.active = True
                self.frames = list(self.pre)
                self.voiced = sum(self.recent)
                self.silence = 0
                return "start", None
            return None, None

        self.frames.append(frame)
        if speech:
            self.voiced += 1
            self.silence = 0
        else:
            self.silence += 1
        if self.silence >= self.end_frames or len(self.frames) >= self.max_frames:
            return self._finish()
        return None, None

    def _finish(self):
        frames, voiced, ptt, silence = self.frames, self.voiced, self.ptt, self.silence
        self.reset()
        self.pre.clear()
        if not ptt and voiced < 10:          # меньше 0.3 с речи — кашель, щелчок
            return "drop", None
        if ptt and len(frames) < 12:
            return "drop", None
        if not ptt and silence > 8:          # хвост тишины не нужен — оставляем 0.24 с
            frames = frames[:len(frames) - (silence - 8)]
        return "end", (b"".join(frames), ptt)


def normalize(raw):
    """Выровнять громкость фразы: тихий микрофон Google слышит хуже."""
    top = peak(raw)
    if top <= 0:
        return raw
    gain = min(8.0, 0.7 * 32767 / top)
    return scale(raw, gain) if gain > 1.1 else raw


# ---------- клавиша ----------

def key_down(vk):
    if not vk or sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except (AttributeError, OSError):
        return False


# ---------- микрофон и распознавание ----------

class VoiceListener:
    def __init__(self, cfg, on_text, on_status=print, on_event=None):
        self.cfg = cfg["voice"]
        self.on_text = on_text                  # on_text([варианты], ptt)
        self.on_status = on_status
        self.on_event = on_event or (lambda e: None)
        self.state = load_state()
        self.ready = False
        self._run = False
        self._thread = None
        self._jobs = queue.Queue()
        self._worker = None
        self._vosk_model = None
        self._device = None
        self.seg = Segmenter(float(self.cfg.get("end_silence", 0.8)), float(self.cfg.get("max_phrase_sec", 12)),
                             self.state["sensitivity"])
        self._event = "idle"

    # ---------- запуск ----------

    def start(self):
        try:
            import pyaudio  # noqa: F401
        except ImportError:
            self.on_status("Голос выключен: не установлен PyAudio.")
            return False
        if self.cfg.get("mode") == "offline" and not self._load_vosk():
            return False
        if self.seg.vad is None:
            self.on_status("Детектор речи WebRTC не найден — определяю речь только по громкости.")
        self._device = self._pick_device()
        self.ready = True
        self._worker = threading.Thread(target=self._recognize_loop, daemon=True)
        self._worker.start()
        if self.state["muted"]:
            self.on_status("Микрофон выключен (из меню) — не слушаю.")
        else:
            self._open()
        return True

    def _pick_device(self):
        import pyaudio
        pa = pyaudio.PyAudio()
        try:
            names = []
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0 and info.get("hostApi", 0) == 0:   # MME — как в системе
                    names.append((i, info.get("name", "")))
            try:
                default = pa.get_default_input_device_info()
                self.on_status(f"Микрофон по умолчанию: {default.get('index')}: {default.get('name')}")
            except (IOError, OSError):
                self.on_status("Микрофона по умолчанию нет.")
            if names:
                self.on_status("Микрофоны: " + "; ".join(f"{i}: {n}" for i, n in names[:12]))
            want = str(self.cfg.get("device_name", "") or "").lower()
            if want:
                for i, n in names:
                    if want in n.lower():
                        self.on_status(f"Выбран микрофон {i}: {n}")
                        return i
                self.on_status(f"Микрофон «{want}» не найден — беру по умолчанию.")
            return None
        finally:
            pa.terminate()

    def _open(self):
        if self._run or not self.ready:
            return
        self._run = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self, wait=True):
        self._run = False
        if wait and self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _emit(self, ev):
        if ev != self._event:
            self._event = ev
            try:
                self.on_event(ev)
            except Exception:  # noqa: BLE001
                pass

    def _capture_loop(self):
        """Читает микрофон, пока _run. Микрофон открыт только здесь — «не слушать» его закрывает."""
        import pyaudio
        pa = pyaudio.PyAudio()
        stream, rate = None, RATE
        try:
            for rate in (RATE, 48000, 44100):
                try:
                    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                                     frames_per_buffer=rate * FRAME_MS // 1000, input_device_index=self._device)
                    break
                except (IOError, OSError, ValueError):
                    stream = None
            if stream is None:
                self.on_status("Голос выключен: не удалось открыть микрофон.")
                self._run = False
                return
            self.on_status(f"Слушаю микрофон (Google, {rate} Гц, чувствительность "
                           f"{SENSITIVITY[self.state['sensitivity']][0]}, клавиша "
                           f"{PTT_KEYS[self.state['ptt_key']][1]}).")
            self.seg.reset()
            buf = b""
            vk = PTT_KEYS[self.state["ptt_key"]][0]
            while self._run:
                try:
                    data = stream.read(rate * FRAME_MS // 1000, exception_on_overflow=False)
                except (IOError, OSError) as e:
                    self.on_status(f"Микрофон отвалился ({e}) — переподключаюсь…")
                    time.sleep(1.0)
                    break
                if rate != RATE:
                    data = resample(data, rate)
                buf += data
                vk = PTT_KEYS[self.state["ptt_key"]][0]
                while len(buf) >= FRAME_BYTES:
                    frame, buf = buf[:FRAME_BYTES], buf[FRAME_BYTES:]
                    kind, payload = self.seg.push(frame, key_down(vk))
                    if kind == "start":
                        self._emit("listening")
                    elif kind == "drop":
                        self._emit("idle")
                    elif kind == "end":
                        self._emit("thinking")
                        self._jobs.put(payload)
        finally:
            try:
                if stream:
                    stream.stop_stream()
                    stream.close()
            except (IOError, OSError):
                pass
            pa.terminate()
            if self._event == "listening":
                self._emit("idle")
        if self._run:                       # отвалился — открываем снова
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()

    def _recognize_loop(self):
        while True:
            raw, ptt = self._jobs.get()
            try:
                variants = self._recognize(normalize(raw))
            except Exception as e:  # noqa: BLE001
                self.on_status(f"Ошибка распознавания: {e}")
                variants = []
            if self._jobs.empty() and self._event == "thinking":
                self._emit("idle")
            if variants and not self.state["muted"]:
                self.on_text(variants, ptt)
            elif ptt:
                self.on_text([], True)          # зажимал клавишу, но слов не разобрать — она скажет

    def _recognize(self, raw):
        if self.cfg.get("mode") == "offline":
            return [self._recognize_vosk(raw)]
        import speech_recognition as sr
        audio = sr.AudioData(raw, RATE, 2)
        r = sr.Recognizer()
        for attempt in range(2):
            try:
                res = r.recognize_google(audio, language="ru-RU", show_all=True)
                break
            except sr.RequestError:
                if attempt:
                    self.on_status("Google Speech недоступен (нет интернета?).")
                    return []
                time.sleep(0.4)
            except sr.UnknownValueError:
                return []
        alts = res.get("alternative", []) if isinstance(res, dict) else []
        out = [a.get("transcript", "").strip().lower() for a in alts]
        return list(dict.fromkeys(v for v in out if v))

    # ---------- управление из меню ----------

    def set_muted(self, muted):
        self.state["muted"] = bool(muted)
        save_state(self.state)
        if not self.ready:
            return
        if muted:
            self.stop()
            self._emit("idle")
            self.on_status("Микрофон выключен — не слушаю.")
        else:
            self._open()

    def set_sensitivity(self, level):
        level = level if level in SENSITIVITY else 1
        self.state["sensitivity"] = level
        save_state(self.state)
        self.seg.set_sensitivity(level)
        self.on_status(f"Чувствительность: {SENSITIVITY[level][0]}.")

    def set_ptt_key(self, key):
        key = key if key in PTT_KEYS else "f8"
        self.state["ptt_key"] = key
        save_state(self.state)
        self.on_status(f"Клавиша «зажми и говори»: {PTT_KEYS[key][1]}.")

    def calibrate(self):
        """Забыть уровень шума — заново подстроится за пару секунд тишины."""
        self.seg.noise = 30.0
        time.sleep(1.5)
        self.on_status(f"Шум подстроен: фон {int(self.seg.noise)}.")

    # ---------- оффлайн (Vosk), если выбран в config.json ----------

    def _load_vosk(self):
        try:
            from vosk import Model, SetLogLevel
        except ImportError:
            self.on_status("Голос выключен: не установлен vosk.")
            return False
        p = self.cfg["vosk_model_path"]
        if not os.path.isabs(p):
            p = path(p)
        if not os.path.isdir(p):
            self.on_status(f"Голос выключен: нет модели Vosk в {p}.")
            return False
        SetLogLevel(-1)
        self._vosk_model = Model(p)
        return True

    def _recognize_vosk(self, raw):
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(self._vosk_model, RATE)
        rec.AcceptWaveform(raw)
        return json.loads(rec.FinalResult()).get("text", "")
