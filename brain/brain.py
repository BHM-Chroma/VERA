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
import webbrowser

import websockets
from rapidfuzz import fuzz

from commands import Commands, run_program
import apps
from llm import LLM
from monitor import Monitor
from voice import VoiceListener
import windows_info
import music
import reminders
import usercmds

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
LAUNCH_WORDS = ("запусти", "открой", "включи")
LAUNCH_STEMS = ("запус", "откр", "включ")      # запусти/запустить/открой/открыть/включи/включить
CLOSE_STEMS = ("закр", "выключ", "выруб", "заверш")   # закрой/выключи/выруби/заверши
DANGER_THRESHOLD = 92      # опасные команды (выключение ПК) — только при почти точном совпадении
ORDINALS = {"перв": 0, "один": 0, "1": 0, "втор": 1, "два": 1, "2": 1, "трет": 2, "три": 2, "3": 2}
CANCEL_WORDS = ("никак", "отмена", "не надо", "ничего", "забей")
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
        self.pending_choice = None     # (варианты, запрос, срок, "open"/"close") — когда переспросила
        self.shutdown_until = 0.0      # идёт отсчёт выключения ПК (до этого времени можно отменить)
        self.apps = apps.AppIndex()
        self.last_interaction = 0.0
        self.quit_event = asyncio.Event()
        self.aliases = [a.lower() for a in cfg["aliases"]]
        self.follow_hwnd = 0
        self._follow_last = None
        self.music_state = {"playing": False}
        self._last_music_comment = 0.0
        self.usercmds = usercmds.UserCommands()
        self.reminders = reminders.Reminders()
        self.pending_teach = None      # (команда, срок) — спросила «что делать по команде …?»
        self.pending_remind = None     # (фраза, срок) — спросила «когда напомнить?»
        self.macro_depth = 0           # своя команда вызывает другие — защита от зацикливания

    # ---------- связь с персонажем ----------

    async def broadcast(self, msg):
        data = json.dumps(msg, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send(data)
            except websockets.ConnectionClosed:
                self.clients.discard(ws)

    async def say(self, text, anim=None, **extra):
        print(f"[{self.cfg['name']}]: {text}")
        msg = {"type": "say", "text": text, **extra}
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
        elif kind == "sleep":
            # заскучала (долго не трогали) — засыпает, как по команде «спи»
            if not self.sleep_mode:
                print("[мозг] её долго не трогали — засыпает")
                self.sleep_mode = True
                await self._sleep_visual(True)
        elif kind == "wake":
            # клик по спящей
            if self.sleep_mode:
                self.sleep_mode = False
                await self._sleep_visual(False)
                await self.say(random.choice(["Ой, я задремала…", "Я тут! Просто прикрыла глаза.", "Мм? Я проснулась."]))
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
            self.last_interaction = time.time()
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

    def _is_alias(self, word):
        """Её имя, даже с опечаткой или как его «услышал» распознаватель: «веронка», «вероника»."""
        word = word.strip(".,!?:;")
        if word in self.aliases:
            return True
        return len(word) >= 5 and any(len(a) >= 5 and fuzz.ratio(word, a) >= 80 for a in self.aliases)

    def _clean(self, text):
        words = [w.strip(".,!?:;") for w in text.lower().replace(",", " ").split()]
        fillers = set(self.cfg["filler_words"])
        kept = [w for w in words if w and w not in fillers and not self._is_alias(w)]
        # «отбой отбой» -> «отбой»: распознавание часто дублирует фразу целиком
        half = len(kept) // 2
        if half and len(kept) % 2 == 0 and kept[:half] == kept[half:]:
            kept = kept[:half]
        dedup = [w for i, w in enumerate(kept) if i == 0 or w != kept[i - 1]]
        return " ".join(dedup).strip()

    async def process(self, text, source):
        text = text.lower().strip()
        now = time.time()
        has_alias = any(a in text for a in self.aliases) or any(self._is_alias(w) for w in text.split())

        if source == "voice":
            if self.sleep_mode:
                if has_alias or "проснись" in text:
                    self.set_sleep(False)
                    await self.say("Я снова здесь.")
                    self.last_interaction = now
                return
            in_dialog = now - self.last_interaction < self.cfg["voice"]["conversation_window_sec"] \
                or any(p and now < p[1] for p in (self.pending_teach, self.pending_remind))
            if self.cfg["voice"]["require_wake_word"] and not has_alias and not in_dialog:
                return
            print(f"[Ты, голосом]: {text}")
        elif self.sleep_mode:
            self.set_sleep(False)

        cmd = self._clean(text)
        self.last_interaction = now
        await self.broadcast({"type": "attention"})   # с ней общаются — ей не скучно

        # идёт отсчёт выключения ПК — «стоп», «нет», «отмена» его отменяют
        if now < self.shutdown_until and (any(w.startswith(("стоп", "стой", "нет", "отмен")) for w in cmd.split())
                                          or "не выключ" in cmd or "не надо" in cmd):
            await self.say(await asyncio.to_thread(self.commands.execute, "cancel_shutdown", cmd))
            return

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

        # ответ на «нашла несколько — какую открыть?»
        if self.pending_choice:
            options, query, deadline, action = self.pending_choice
            self.pending_choice = None
            if now < deadline:
                picked = self._pick_option(cmd, options)
                if picked:
                    if action == "close":
                        await self._close_app(picked)
                    else:
                        self.apps.remember_choice(query, picked)
                        await self._launch_app(picked)
                    return
                if any(w in cmd for w in CANCEL_WORDS):
                    await self.say("Хорошо, ничего не делаю.")
                    return

        # свои команды («запомни команду …») и напоминания («напомни через …», «таймер на …»)
        raw = " ".join(w for w in text.split() if not self._is_alias(w))
        if await self._user_command_flow(raw, cmd, now):
            return
        if await self._reminder_flow(raw, cmd, now):
            return

        if not cmd:
            await self.say("Да? Я слушаю.")
            return

        key, score = self.commands.recognize(cmd)

        danger = bool(key) and self.commands.is_danger(key)
        exact = score >= (DANGER_THRESHOLD if danger else 85)

        # «закрой X» / «выключи X» — закрыть открытую программу
        # «запусти X» / «открой X» — запустить (слово может стоять и не первым: «а запусти-ка X»)
        # Сначала точные команды («выключи музыку», «выключи компьютер»), потом программы.
        if not exact:
            words = cmd.split()
            for i, w in enumerate(words):
                if i + 1 < len(words) and w.startswith(CLOSE_STEMS):
                    await self.close_app(" ".join(words[i + 1:]))
                    return
                if i + 1 < len(words) and w.startswith(LAUNCH_STEMS):
                    await self.open_app(" ".join(words[i + 1:]))
                    return

        if key and score >= COMMAND_THRESHOLD and (exact or not danger):
            if danger:
                self.pending = (key, cmd, now + 15)
                await self.say(f"{self.commands.title(key)}? Скажи «да», чтобы подтвердить.")
                return
            await self.say(await asyncio.to_thread(self.commands.execute, key, cmd))
            return

        ctx = f"Сейчас состояние компьютера: {self.monitor.describe()}"
        answer = await asyncio.to_thread(self.llm.ask, cmd, ctx)
        await self.say(answer)
        self.last_interaction = time.time()

    # ---------- свои команды ----------

    def _known_step(self, step):
        if usercmds.url_of(step) or usercmds.say_text(step):
            return True
        c = self._clean(step)
        if not c:
            return False
        return c.split()[0].startswith(LAUNCH_STEMS + CLOSE_STEMS) or self.usercmds.find(c) is not None \
            or self.commands.recognize(c)[1] >= COMMAND_THRESHOLD or reminders.is_request(c)

    async def _user_command_flow(self, raw, cmd, now):
        if self.pending_teach:
            trig, deadline = self.pending_teach
            self.pending_teach = None
            if now < deadline:
                if any(w in CANCEL_WORDS for w in cmd.split()) or "не надо" in cmd:
                    await self.say("Хорошо, не запоминаю.")
                else:
                    await self._teach(trig, raw)
                return True
        m = usercmds.TEACH_RE.search(raw)
        if m:
            await self._teach(m.group("trig"), m.group("act"))
            return True
        m = usercmds.TEACH_ONLY_RE.search(raw)
        if m:
            trig = self._clean(usercmds.strip_quotes(m.group("trig")))
            if not trig:
                await self.say("Скажи так: «Запомни команду игровой режим, её действие запусти стим и спрячься».")
                return True
            self.pending_teach = (trig, now + 40)
            await self.say(f"Что мне делать по команде «{trig}»?")
            return True
        m = usercmds.FORGET_RE.search(raw)
        if m:
            trig = self._clean(usercmds.strip_quotes(m.group("trig")))
            removed = self.usercmds.remove(trig)
            await self.say(f"Забыла команду «{removed['trigger']}»." if removed else f"Я не помню команды «{trig}».")
            return True
        item = self.usercmds.find(cmd) if cmd else None
        if item:
            await self.run_user_command(item)
            return True
        return False

    async def _teach(self, trig, action):
        trigger = self._clean(usercmds.strip_quotes(trig))
        steps, verb = [], None
        for st in usercmds.split_action(action.lower()):
            first = st.split()[0]
            if first.startswith(LAUNCH_STEMS + CLOSE_STEMS):
                verb = first
            elif verb and not self._known_step(st):
                st = f"{verb} {st}"              # «открой телеграм и спотифай» -> «открой спотифай»
            steps.append(st)
        if not trigger or not steps:
            await self.say("Не поняла. Скажи: «Запомни команду …, её действие …».")
            return
        if any(self._clean(st) == trigger for st in steps):
            await self.say("Команда не может вызывать саму себя.")
            return
        existed = self.usercmds.add(trigger, steps)
        reply = f"{'Обновила' if existed else 'Запомнила'}: «{trigger}» — {', '.join(steps)}."
        unknown = [st for st in steps if not self._known_step(st)]
        if unknown:
            reply += f" «{unknown[0]}» я не знаю как команду — на это просто отвечу."
        print(f"[команды] {trigger!r} -> {steps}")
        await self.say(reply)

    async def run_user_command(self, item):
        if self.macro_depth >= 3:
            await self.say("Мои команды вызывают друг друга по кругу — остановилась.")
            return
        print(f"[команды] выполняю {item['trigger']!r}: {item['steps']}")
        self.macro_depth += 1
        try:
            for i, st in enumerate(item["steps"]):
                if i:
                    await asyncio.sleep(1.2)     # чтобы облачка успели прочитать
                text, url = usercmds.say_text(st), usercmds.url_of(st)
                if text:
                    await self.say(text[0].upper() + text[1:])
                elif url:
                    await asyncio.to_thread(webbrowser.open, url)
                    await self.say(f"Открываю {url.split('//', 1)[-1].split('/')[0]}.")
                else:
                    await self.process(st, source="macro")
        finally:
            self.macro_depth -= 1

    # ---------- напоминания и таймеры ----------

    async def _reminder_flow(self, raw, cmd, now):
        words = cmd.split()
        about = any(w.startswith(("напомин", "таймер", "будильник")) for w in words)

        if self.pending_remind:
            first, deadline = self.pending_remind
            self.pending_remind = None
            if now < deadline:
                if any(w in CANCEL_WORDS for w in words) or "не надо" in cmd:
                    await self.say("Хорошо, не напоминаю.")
                    return True
                r = reminders.parse(first + " " + raw, now)
                if r:
                    await self._add_reminder(r, now)
                    return True

        if about and any(w.startswith(("какие", "мои", "список", "покажи", "есть")) for w in words) \
                or "что ты должна напомнить" in cmd:
            items = self.reminders.upcoming()
            if not items:
                await self.say("Напоминаний и таймеров нет.")
            else:
                listing = "; ".join(reminders.describe(r, now) for r in items[:5])
                more = f" И ещё {len(items) - 5}." if len(items) > 5 else ""
                await self.say(f"Есть: {listing}.{more}", duration=min(15, 4 + 2.5 * len(items[:5])))
            return True

        if about and any(w.startswith(("отмен", "удали", "убери", "сбрось", "выключи", "останови", "сотри")) for w in words):
            if any(w in ("все", "всё") for w in words):
                n = self.reminders.clear()
                await self.say(f"Убрала всё ({n})." if n else "Напоминаний и так нет.")
                return True
            kind = "timer" if "таймер" in cmd else "alarm" if "будильник" in cmd else None
            query = " ".join(w for w in words if not w.startswith(("отмен", "удали", "убери", "сбрось", "выключи",
                                                                     "останови", "сотри", "напомин", "таймер", "будильник", "про", "о", "об")))
            gone = self.reminders.cancel(query, kind)
            await self.say(f"Отменила: {reminders.describe(gone[0], now)}." if gone else
                           {"timer": "Таймеров нет.", "alarm": "Будильников нет."}.get(kind, "Нечего отменять."))
            return True

        if "сколько осталось" in cmd or ("сколько" in text_words(raw) and "осталось" in cmd):
            items = self.reminders.upcoming()
            timers = [r for r in items if r["kind"] == "timer"] or items
            if not timers:
                await self.say("Сейчас ничего не засечено.")
            else:
                r = timers[0]
                await self.say(f"Осталось {reminders.left_words(r['when'] - now)}"
                               + (f" — {reminders.what_words(r)}." if r["text"] else "."))
            return True

        if reminders.is_request(raw):
            r = reminders.parse(raw, now)
            if r:
                await self._add_reminder(r, now)
            else:
                self.pending_remind = (raw, now + 40)
                await self.say("Когда напомнить? Например: «через 10 минут» или «в 18:30».")
            return True
        return False

    async def _add_reminder(self, r, now):
        self.reminders.add(r)
        print(f"[напоминания] {reminders.describe(r, now)}")
        await self.say(reminders.confirm_text(r, now))

    async def reminders_loop(self):
        # что пропустили, пока программа была закрыта
        missed = self.reminders.due()
        for _ in range(30):
            if self.clients:
                break
            await asyncio.sleep(1)
        await asyncio.sleep(3)
        recent = [r for r in missed if time.time() - r["when"] < 12 * 3600]
        if recent:
            await self._fire(recent[0], prefix="Пока меня не было: ")
            for r in recent[1:]:
                await asyncio.sleep(6)
                await self._fire(r, prefix="И ещё: ")
        while True:
            for r in self.reminders.due():
                await self._fire(r)
                await asyncio.sleep(2)
            await asyncio.sleep(1)

    async def _fire(self, r, prefix=""):
        if self.sleep_mode:
            self.sleep_mode = False
            await self._sleep_visual(False)
        text = prefix + reminders.fire_text(r)
        fired = time.time()
        await asyncio.to_thread(chime)
        await self.say(text, urgent=True, duration=8.0)

        async def again():          # не ответили и не тронули — повторит один раз через минуту
            await asyncio.sleep(60)
            if self.last_interaction < fired:
                await asyncio.to_thread(chime)
                await self.say("Ещё раз: " + reminders.fire_text(r), urgent=True, duration=8.0)
        asyncio.create_task(again())

    # ---------- программы ----------

    async def open_app(self, name):
        found = self.apps.find(name)
        if not found:
            if not self.apps.entries:
                await self.say(f"Ищу {name}…")   # список ещё строится — по-старому, поиском по диску
            await self.say(await asyncio.to_thread(run_program, name))
            return
        best, best_score = found[0]
        rivals = [e for e, sc in found[1:] if sc >= 80 and best_score - sc < 6]
        if not rivals:
            await self._launch_app(best)
            return
        options = [best] + rivals
        listing = ", ".join(f"{i + 1}) {e['name']}" for i, e in enumerate(options))
        self.pending_choice = (options, name, time.time() + 20, "open")
        await self.say(f"Нашла несколько: {listing}. Какую открыть?")

    async def close_app(self, name):
        found = await asyncio.to_thread(apps.find_running, name)
        if not found:
            await self.say(f"Не вижу открытой программы «{name}».")
            return
        best, best_score = found[0]
        rivals = [w for w, sc in found[1:] if best_score - sc < 6]
        if not rivals:
            await self._close_app(best)
            return
        options = [best] + rivals
        for o in options:
            o["name"] = f"{apps.display_name(o)} («{o['title'][:30]}»)"
        listing = ", ".join(f"{i + 1}) {o['name']}" for i, o in enumerate(options))
        self.pending_choice = (options, name, time.time() + 20, "close")
        await self.say(f"Открыто несколько: {listing}. Какую закрыть?")

    async def _close_app(self, window):
        await asyncio.to_thread(apps.close, window)
        await self.say(f"Закрываю {apps.display_name(window)}.")

    async def _launch_app(self, entry):
        try:
            await asyncio.to_thread(apps.launch, entry)
            await self.say(f"Запускаю {entry['name']}.")
        except OSError as e:
            await self.say(f"Не получилось запустить {entry['name']}: {e}")

    @staticmethod
    def _pick_option(answer, options):
        for word, idx in ORDINALS.items():
            if any(w.startswith(word) for w in answer.split()) and idx < len(options):
                return options[idx]
        best, best_s = None, 0
        for e in options:
            s = max(fuzz.partial_ratio(answer, e["name"].lower()),
                    fuzz.partial_ratio(apps.translit(answer), e["name"].lower()))
            if s > best_s:
                best, best_s = e, s
        return best if best_s >= 75 else None

    # ---------- фоновые задачи ----------

    async def active_window_loop(self):
        """Следим, в каком окне работает человек (для сидения на окне),
        и не открыто ли что-то во весь экран (тогда она прячется)."""
        fullscreen = False
        while True:
            try:
                windows_info.remember_active()
                fs = windows_info.foreground_fullscreen()
            except OSError:
                fs = fullscreen
            if fs != fullscreen:
                fullscreen = fs
                print(f"[окна] полноэкранное окно — прячусь: {windows_info.fullscreen_reason}" if fs
                      else "[окна] полноэкранное закрыто — появляюсь")
                await self.broadcast({"type": "fullscreen", "active": fs})
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

    async def apps_loop(self):
        """Раз в час проверяем, не пора ли обновить список программ (раз в сутки)."""
        while True:
            await asyncio.sleep(3600)
            self.apps.refresh()

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
            self.apps.refresh()   # список программ — в фоне (если старше суток)
            apps_task = asyncio.create_task(self.apps_loop())
            rem_task = asyncio.create_task(self.reminders_loop())
            listener = await asyncio.to_thread(self.start_voice)
            await self.quit_event.wait()
            await self.broadcast({"type": "quit"})
            await asyncio.sleep(0.5)   # дать персонажу получить сообщение до закрытия
            mon.cancel()
            win_task.cancel()
            follow_task.cancel()
            music_task.cancel()
            apps_task.cancel()
            rem_task.cancel()
            if listener:
                listener.stop()


def text_words(text):
    return [w.strip(".,!?:;") for w in text.split()]


def chime():
    """Звук уведомления Windows — чтобы напоминание услышали, даже если не смотрят на экран."""
    if sys.platform != "win32":
        return
    try:
        import winsound
        winsound.PlaySound("SystemNotification", winsound.SND_ALIAS)
        time.sleep(0.15)
        winsound.PlaySound("SystemNotification", winsound.SND_ALIAS)
    except (RuntimeError, ImportError):
        pass


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
