"""VERONIKA — «мозг» desktop mate.

Запускает WebSocket-сервер (ws://127.0.0.1:8765), к которому подключается окно
с персонажем (Godot). Слушает микрофон, выполняет команды, болтает через ИИ,
следит за нагрузкой ПК и сообщает персонажу, что сказать и какую анимацию играть.

Запуск:  python brain.py
"""
import asyncio
import json
import os
import random
import sys
import time

import websockets

from commands import Commands, run_program
from llm import LLM
from monitor import Monitor
from voice import VoiceListener
import windows_info
import music

from paths import BASE_DIR

if sys.platform == "win32":
    # чтобы русский текст нормально печатался в консоли Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


def load_env(path):
    """Мини-загрузчик .env: строки вида KEY=value."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config():
    """config.json — общие настройки (в репозитории),
    config.local.json — твои личные (ссылки, пороги); его обновления не трогают."""
    with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    local = os.path.join(BASE_DIR, "config.local.json")
    if os.path.exists(local):
        with open(local, encoding="utf-8") as f:
            _merge(cfg, json.load(f))
    return cfg


POKE_LINES = [
    "Эй, щекотно!",
    "Я здесь, слежу за компьютером.",
    "Что-то нужно? Просто позови меня по имени.",
    "Нажми правой кнопкой, если хочешь написать мне.",
]
LAUNCH_WORDS = ("запусти", "открой")
COMMAND_THRESHOLD = 70


class Brain:
    def __init__(self, cfg):
        self.cfg = cfg
        self.monitor = Monitor(cfg)
        self.commands = Commands(cfg, self)
        self.llm = LLM(cfg)
        self.clients = set()
        self.loop = None
        self.sleep_mode = False
        self.pending = None            # (ключ команды, текст, срок подтверждения)
        self.last_interaction = 0.0
        self.quit_event = asyncio.Event()
        self.aliases = [a.lower() for a in cfg["aliases"]]
        self.follow_hwnd = 0
        self._follow_last = None
        self.music_state = {"playing": False}
        self._last_music_comment = 0.0

    # ---------- связь с персонажем ----------

    async def broadcast(self, msg):
        data = json.dumps(msg, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send(data)
            except websockets.ConnectionClosed:
                self.clients.discard(ws)

    async def say(self, text, anim=None):
        print(f"[{self.cfg['name']}]: {text}")
        msg = {"type": "say", "text": text}
        if anim:
            msg["anim"] = anim
        await self.broadcast(msg)

    async def handle_client(self, ws):
        self.clients.add(ws)
        print("[мозг] персонаж подключился")
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self.on_message(msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)
            print("[мозг] персонаж отключился")

    async def on_message(self, msg):
        kind = msg.get("type")
        if kind == "hello":
            windows_info.set_own_pid(msg.get("pid"))
            await self.broadcast({"type": "stats", **self.monitor.stats})
            if self.music_state.get("playing"):
                await self.broadcast({"type": "music", **self.music_state})
        elif kind == "chat":
            text = str(msg.get("text", ""))
            print(f"[Ты]: {text}")
            await self.process(text, source="chat")
        elif kind == "shutdown":
            print("[мозг] персонаж закрыт через меню — завершаюсь")
            self.quit_event.set()
        elif kind == "follow_window":
            # персонаж сел на окно — сами сообщаем, когда оно сдвинется
            self.follow_hwnd = int(msg.get("hwnd") or 0)
            self._follow_last = "start"
        elif kind == "window_at":
            info = windows_info.window_at(int(msg.get("x", 0)), int(msg.get("y", 0)), int(msg.get("tol", 45)))
            print(f"[окна] отпустили у окна: {info['process']} «{info['title'][:40]}»" if info
                  else "[окна] отпустили не у края окна")
            await self.broadcast({"type": "window_info", "reply_to": msg.get("id"), "window": info})
        elif kind == "window_query":
            hwnd = msg.get("hwnd")
            info = windows_info.describe(int(hwnd)) if hwnd else windows_info.pick_window()
            if not hwnd:
                print(f"[окна] сесть на: {info['process']} «{info['title'][:40]}» "
                      f"{info['x']},{info['y']} {info['w']}x{info['h']}" if info else "[окна] подходящего окна нет")
            await self.broadcast({"type": "window_info", "reply_to": msg.get("id"), "window": info})
        elif kind == "poke":
            if self.sleep_mode:
                await self.say("Мм… я сплю. Позови меня по имени.")
            else:
                await self.say(random.choice(POKE_LINES + [self.monitor.describe()]))

    # ---------- режимы ----------

    def set_sleep(self, value):
        self.sleep_mode = value
        asyncio.run_coroutine_threadsafe(self._sleep_visual(value), self.loop)

    async def _sleep_visual(self, value):
        await self.broadcast({"type": "anim", "name": "sleep" if value else "idle"})
        await self.broadcast({"type": "face", "index": 2 if value else -1})

    def send_threadsafe(self, msg):
        """Отправить сообщение персонажу из потока команды."""
        asyncio.run_coroutine_threadsafe(self.broadcast(msg), self.loop)

    def request_quit(self):
        # вызывается из потока команды, поэтому через call_soon_threadsafe
        self.loop.call_soon_threadsafe(self.loop.call_later, 2.5, self.quit_event.set)

    # ---------- обработка фраз ----------

    def _clean(self, text):
        words = [w for w in text.lower().replace(",", " ").split()]
        drop = set(self.aliases) | set(self.cfg["filler_words"])
        kept = [w for w in words if w not in drop]
        # «отбой отбой» -> «отбой»: распознавание часто дублирует фразу целиком
        half = len(kept) // 2
        if half and len(kept) % 2 == 0 and kept[:half] == kept[half:]:
            kept = kept[:half]
        dedup = [w for i, w in enumerate(kept) if i == 0 or w != kept[i - 1]]
        return " ".join(dedup).strip()

    async def process(self, text, source):
        text = text.lower().strip()
        now = time.time()
        has_alias = any(a in text for a in self.aliases)

        if source == "voice":
            if self.sleep_mode:
                if has_alias or "проснись" in text:
                    self.set_sleep(False)
                    await self.say("Я снова здесь.")
                    self.last_interaction = now
                return
            in_dialog = now - self.last_interaction < self.cfg["voice"]["conversation_window_sec"]
            if self.cfg["voice"]["require_wake_word"] and not has_alias and not in_dialog:
                return
            print(f"[Ты, голосом]: {text}")
        elif self.sleep_mode:
            self.set_sleep(False)

        cmd = self._clean(text)
        self.last_interaction = now

        # подтверждение опасной команды
        if self.pending:
            key, ptext, deadline = self.pending
            self.pending = None
            if now < deadline and any(w in self.cfg["yes_words"] for w in cmd.split()):
                await self.say(await asyncio.to_thread(self.commands.execute, key, ptext))
                return
            if now < deadline:
                await self.say("Хорошо, не буду.")
                return

        if not cmd:
            await self.say("Да? Я слушаю.")
            return

        key, score = self.commands.recognize(cmd)

        # «запусти X» / «открой X» — сначала точные команды, потом поиск программы
        if cmd.startswith(LAUNCH_WORDS) and score < 85:
            name = cmd.split(maxsplit=1)[1] if " " in cmd else ""
            if name:
                await self.say(f"Ищу {name}…")
                await self.say(await asyncio.to_thread(run_program, name))
                return

        if key and score >= COMMAND_THRESHOLD:
            if self.commands.is_danger(key):
                self.pending = (key, cmd, now + 15)
                await self.say(f"Точно выполнить «{cmd}»? Скажи «да».")
                return
            await self.say(await asyncio.to_thread(self.commands.execute, key, cmd))
            return

        ctx = f"Сейчас состояние компьютера: {self.monitor.describe()}"
        answer = await asyncio.to_thread(self.llm.ask, cmd, ctx)
        await self.say(answer)
        self.last_interaction = time.time()

    # ---------- фоновые задачи ----------

    async def active_window_loop(self):
        """Следим, в каком окне работает человек (для сидения на окне)."""
        while True:
            try:
                windows_info.remember_active()
            except OSError:
                pass
            await asyncio.sleep(0.5)

    async def follow_loop(self):
        """Окно, на котором она сидит: ~100 раз в секунду проверяем, не сдвинулось ли,
        и сразу сообщаем персонажу (без запросов с его стороны — так нет задержки)."""
        while True:
            hwnd = self.follow_hwnd
            if not hwnd:
                await asyncio.sleep(0.05)
                continue
            try:
                info = windows_info.quick_rect(hwnd)
            except OSError:
                info = None
            if info != self._follow_last and hwnd == self.follow_hwnd:
                self._follow_last = info
                await self.broadcast({"type": "window_moved", "hwnd": hwnd, "window": info})
                if info is None:
                    self.follow_hwnd = 0
            await asyncio.sleep(0.01)

    async def music_loop(self):
        """Раз в секунду смотрим, что играет. Сообщаем персонажу (анимации, ноты)
        и иногда комментируем новый трек."""
        first = True
        while True:
            try:
                st = await music.get_state()
            except Exception as e:  # noqa: BLE001
                print(f"[музыка] ошибка: {e}")
                st = {"playing": False, "title": "", "artist": "", "app": ""}
            prev = self.music_state
            changed_track = st["playing"] and (st["title"], st["artist"]) != (prev.get("title"), prev.get("artist"))
            if st["playing"] != prev.get("playing") or changed_track:
                self.music_state = st
                await self.broadcast({"type": "music", **st})
                if st["playing"]:
                    print(f"[музыка] играет: {st['artist']} — {st['title']} ({st['app']})")
                    if changed_track and not first:
                        await self._comment_track(st)
                else:
                    print("[музыка] пауза / тишина")
            first = False
            await asyncio.sleep(1.0)

    async def _comment_track(self, st):
        mcfg = self.cfg.get("music", {})
        if self.sleep_mode or not mcfg.get("comments", True) or not st["title"]:
            return
        now = time.time()
        if now - self._last_music_comment < mcfg.get("comment_cooldown_sec", 180):
            return
        if random.random() > mcfg.get("comment_chance", 0.4):
            return
        self._last_music_comment = now
        t, a = st["title"], st["artist"]
        lines = [
            f"О, «{t}»! Хороший выбор.",
            f"{a} — «{t}». Мне нравится!",
            f"Ммм, «{t}»… под это приятно работать.",
            f"Включили {a}? Я тоже послушаю.",
            f"«{t}» — сделать погромче?",
        ] if a else [f"О, «{t}»! Хороший выбор.", f"Ммм, «{t}»… под это приятно работать."]
        await self.say(random.choice(lines))

    def music_info(self):
        st = self.music_state
        if not st.get("playing"):
            return "Сейчас ничего не играет."
        return f"Играет «{st['title']}» — {st['artist']}." if st.get("artist") else f"Играет «{st['title']}»."

    async def monitor_loop(self):
        while True:
            stats = await asyncio.to_thread(self.monitor.sample)
            await self.broadcast({"type": "stats", **stats})
            if not self.sleep_mode:
                for alert in await asyncio.to_thread(self.monitor.check_alerts):
                    await self.say(alert, anim="alarm")
            await asyncio.sleep(self.cfg["monitor"]["interval_sec"])

    def start_voice(self):
        if not self.cfg["voice"]["enabled"]:
            print("[мозг] голос выключен в config.json")
            return None

        def on_text(t):
            asyncio.run_coroutine_threadsafe(self.process(t, source="voice"), self.loop)

        listener = VoiceListener(self.cfg, on_text, on_status=lambda s: print(f"[голос] {s}"))
        listener.start()
        return listener

    async def run(self):
        self.loop = asyncio.get_running_loop()
        host, port = self.cfg["ws_host"], self.cfg["ws_port"]
        async with websockets.serve(self.handle_client, host, port):
            print(f"[мозг] жду персонажа на ws://{host}:{port}")
            mon = asyncio.create_task(self.monitor_loop())
            win_task = asyncio.create_task(self.active_window_loop())
            follow_task = asyncio.create_task(self.follow_loop())
            music_task = asyncio.create_task(self.music_loop())
            listener = await asyncio.to_thread(self.start_voice)
            await self.quit_event.wait()
            await self.broadcast({"type": "quit"})
            await asyncio.sleep(0.5)   # дать персонажу получить сообщение до закрытия
            mon.cancel()
            win_task.cancel()
            follow_task.cancel()
            music_task.cancel()
            if listener:
                listener.stop()


class Tee:
    """Печатает в консоль и одновременно пишет в brain.log."""

    def __init__(self, stream, path):
        self.stream = stream
        self.file = open(path, "a", encoding="utf-8", buffering=1)

    def write(self, text):
        try:
            self.stream.write(text)
        except (OSError, ValueError, AttributeError):
            pass
        self.file.write(text)

    def flush(self):
        try:
            self.stream.flush()
        except (OSError, ValueError, AttributeError):
            pass


def main():
    sys.stdout = Tee(sys.stdout, os.path.join(BASE_DIR, "brain.log"))
    sys.stderr = sys.stdout   # ошибки тоже в лог (у собранного .exe нет консоли)
    print(f"\n===== запуск {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    load_env(os.path.join(BASE_DIR, ".env"))
    cfg = load_config()
    try:
        asyncio.run(Brain(cfg).run())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"[мозг] не удалось запустить сервер: {e}. Может, brain.py уже запущен?")


if __name__ == "__main__":
    main()
