"""Команды управления ПК (перенесены из vera_4.py).

Каждая команда: список фраз + функция. Функция возвращает текст ответа.
Опасные команды (danger=True) выполняются только после подтверждения «да».
"""
import datetime
import json
import os
import subprocess
import sys
import time
import webbrowser

from rapidfuzz import fuzz

IS_WINDOWS = sys.platform == "win32"
from paths import BASE_DIR
AUTO_PATHS_FILE = os.path.join(BASE_DIR, "auto_program_paths.json")


# ---------- громкость (pycaw) ----------

def _volume_interface():
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    speakers = AudioUtilities.GetSpeakers()
    if hasattr(speakers, "EndpointVolume"):          # новые версии pycaw
        return speakers.EndpointVolume
    from ctypes import POINTER, cast                  # старые версии pycaw
    from comtypes import CLSCTX_ALL
    iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def get_volume():
    return round(_volume_interface().GetMasterVolumeLevelScalar() * 100)


def set_volume(value):
    value = max(0, min(100, int(value)))
    _volume_interface().SetMasterVolumeLevelScalar(value / 100, None)
    return value


# ---------- яркость (wmi, работает на ноутбуках) ----------

def get_brightness():
    import wmi
    return wmi.WMI(namespace="wmi").WmiMonitorBrightness()[0].CurrentBrightness


def set_brightness(value):
    import wmi
    value = max(0, min(100, int(value)))
    wmi.WMI(namespace="wmi").WmiMonitorBrightnessMethods()[0].WmiSetBrightness(value, 0)
    return value


# ---------- поиск и запуск программ ----------

def _load_paths():
    if os.path.exists(AUTO_PATHS_FILE):
        try:
            with open(AUTO_PATHS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save_paths(paths):
    with open(AUTO_PATHS_FILE, "w", encoding="utf-8") as f:
        json.dump(paths, f, ensure_ascii=False, indent=4)


def _start_menu_dirs():
    return [
        os.path.join(os.environ.get("ProgramData", ""), r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        os.path.expanduser(r"~\Desktop"),
    ]


def find_program(name):
    """Сначала ищем ярлык в меню «Пуск» (быстро), потом .exe на диске (медленно)."""
    name = name.lower().strip()
    best, best_score = None, 0
    for base in _start_menu_dirs():
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.lower().endswith((".lnk", ".url", ".exe")):
                    score = fuzz.partial_ratio(name, os.path.splitext(f)[0].lower())
                    if score > best_score:
                        best, best_score = os.path.join(root, f), score
    if best and best_score >= 80:
        return best

    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"), os.environ.get("APPDATA", "")):
        if not base:
            continue
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.lower().endswith(".exe") and name in f.lower():
                    return os.path.join(root, f)
    return None


def run_program(name):
    paths = _load_paths()
    path = paths.get(name)
    if not path or not os.path.exists(path):
        path = find_program(name)
        if not path:
            return f"Не нашла программу «{name}»."
        paths[name] = path
        _save_paths(paths)
    os.startfile(path)
    return f"Запускаю {name}."


# ---------- реестр команд ----------

class Commands:
    def __init__(self, cfg, brain):
        self.cfg = cfg
        self.brain = brain   # нужен для режима сна и выхода
        self.table = {}
        self._register_builtin()
        for key, link in cfg.get("links", {}).items():
            self.add(key, link["phrases"], self._link_action(link["url"], link.get("reply", "Открываю")))

    def add(self, key, phrases, func, danger=False, title=None):
        """danger=True — перед выполнением спросить «да», title — что именно будет сделано."""
        self.table[key] = {"phrases": tuple(phrases), "func": func, "danger": danger, "title": title}

    def title(self, key):
        return self.table[key].get("title") or key

    @staticmethod
    def _link_action(url, reply):
        def act(_text):
            webbrowser.open(url)
            return reply
        return act

    def recognize(self, text):
        """Возвращает (ключ_команды, процент_совпадения)."""
        best, best_p = None, 0
        for key, c in self.table.items():
            for phrase in c["phrases"]:
                p = fuzz.ratio(text, phrase)
                if p > best_p:
                    best, best_p = key, p
        return best, best_p

    def is_danger(self, key):
        return self.table[key]["danger"]

    def execute(self, key, text):
        try:
            return self.table[key]["func"](text)
        except ImportError as e:
            return f"Для этой команды не хватает библиотеки: {e.name}."
        except Exception as e:  # noqa: BLE001 — показываем любую ошибку в облачке
            return f"Ошибка при выполнении: {e}"

    # --- встроенные команды ---
    def _register_builtin(self):
        def numbers(text):
            return [int(s) for s in text.replace("%", " ").split() if s.isdigit()]

        def ctime(_):
            now = datetime.datetime.now()
            return f"Сейчас {now.hour}:{now.minute:02d}."

        def chrome(_):
            if IS_WINDOWS:
                os.system('start "" chrome')
            else:
                webbrowser.open("about:blank")
            return "Открываю Chrome."

        def vol_up(text):
            n = numbers(text)     # «громче на 30» — на 30, просто «громче» — на 10
            return f"Громкость {set_volume(get_volume() + (n[0] if n else 10))}%."

        def vol_down(text):
            n = numbers(text)
            return f"Громкость {set_volume(get_volume() - (n[0] if n else 10))}%."

        def vol_set(text):
            n = numbers(text)
            return f"Громкость {set_volume(n[0])}%." if n else "Скажи число, например «громкость на 40»."

        def br_up(_):
            return f"Яркость {set_brightness(get_brightness() + 10)}%."

        def br_down(_):
            return f"Яркость {set_brightness(get_brightness() - 10)}%."

        def br_set(text):
            n = numbers(text)
            return f"Яркость {set_brightness(n[0])}%." if n else "Скажи число, например «яркость на 70»."

        def lock(_):
            os.system("rundll32.exe user32.dll,LockWorkStation")
            return "Блокирую экран."

        def lang(_):
            import keyboard
            keyboard.press_and_release("alt+shift")
            return "Язык переключен."

        def shutdown_pc(_):
            subprocess.run(["shutdown", "/s", "/t", "60"], check=False)
            self.brain.shutdown_until = time.time() + 60
            return "Выключаю компьютер через минуту. Скажи «стоп» или «отмена», если передумал."

        def cancel_shutdown(_):
            subprocess.run(["shutdown", "/a"], check=False)
            self.brain.shutdown_until = 0
            return "Выключение отменено."

        def sleep(_):
            self.brain.set_sleep(True)
            return "Ухожу в режим сна. Позови меня по имени."

        def wake(_):
            self.brain.set_sleep(False)
            return "Я снова здесь."

        def stats(_):
            return self.brain.monitor.describe()

        def wander_on(_):
            self.brain.send_threadsafe({"type": "wander", "enabled": True, "now": True})
            return "Пойду прогуляюсь по экрану."

        def wander_off(_):
            self.brain.send_threadsafe({"type": "wander", "enabled": False})
            return "Хорошо, стою на месте."

        def music_pause(_):
            import music
            if not self.brain.music_state.get("playing"):
                return "Музыка и так не играет."
            music.press("play_pause")
            return "Пауза."

        def music_play(_):
            import music
            if self.brain.music_state.get("playing"):
                return "Музыка уже играет."
            music.press("play_pause")
            return "Включаю музыку."

        def music_next(_):
            import music
            music.press("next")
            return "Следующий трек."

        def music_prev(_):
            import music
            music.press("prev")
            return "Предыдущий трек."

        def music_what(_):
            return self.brain.music_info()

        def hide(_):
            self.brain.send_threadsafe({"type": "visibility", "visible": False})
            return "Прячусь. Я рядом — позови по имени или кликни значок у часов."

        def show(_):
            self.brain.send_threadsafe({"type": "visibility", "visible": True})
            return "Я тут!"

        def autohide_on(_):
            self.brain.send_threadsafe({"type": "autohide", "enabled": True})
            return "Хорошо, в полноэкранных играх буду прятаться."

        def autohide_off(_):
            self.brain.send_threadsafe({"type": "autohide", "enabled": False})
            return "Хорошо, больше не прячусь в играх — буду рядом."

        def apps_refresh(_):
            self.brain.apps.refresh(force=True)
            return "Обновляю список программ, это займёт несколько секунд."

        def apps_count(_):
            n = len(self.brain.apps.entries)
            return f"Я знаю {n} программ и игр." if n else "Список программ ещё собирается."

        def my_cmds(_):
            items = self.brain.usercmds.items
            if not items:
                return "Своих команд пока нет. Скажи: «Запомни команду …, её действие …»."
            listing = "; ".join(f"«{c['trigger']}» — {', '.join(c['steps'])}" for c in items[:6])
            return f"Помню: {listing}." + (f" И ещё {len(items) - 6}." if len(items) > 6 else "")

        def forget_all(_):
            n = self.brain.usercmds.clear()
            return f"Забыла все свои команды ({n})." if n else "Своих команд и так нет."

        def quit_brain(_):
            self.brain.request_quit()
            return "Хорошо, завершаю работу. До следующего запуска!"

        self.add("ctime", ("текущее время", "сейчас времени", "который час", "сколько времени"), ctime)
        self.add("open_chrome", ("открой хром", "хром", "открой браузер", "включи chrome"), chrome)
        self.add("volume_up", ("сделай громче", "увеличь громкость", "громче", "громче на", "сделай громче на",
                               "прибавь громкость", "прибавь звук"), vol_up)
        self.add("volume_down", ("сделай тише", "уменьши громкость", "тише", "тише на", "сделай тише на",
                                 "убавь громкость", "убавь звук"), vol_down)
        self.add("volume_set", ("поставь громкость", "громкость на"), vol_set)
        self.add("brightness_up", ("ярче", "увеличь яркость"), br_up)
        self.add("brightness_down", ("темнее", "уменьши яркость"), br_down)
        self.add("brightness_set", ("установи яркость на", "яркость на"), br_set)
        self.add("lock", ("заблокируй экран", "заблокируй компьютер", "заблокируй", "блокировка"), lock)
        self.add("langswitch", ("поменяй язык", "смени язык", "смена языка"), lang)
        self.add("shutdown_pc", ("выключи компьютер", "выключи пк", "отключи компьютер", "отключи пк"),
                 shutdown_pc, danger=True, title="Выключить компьютер")
        self.add("cancel_shutdown", ("отмена выключения", "отмени выключение", "не выключай"), cancel_shutdown)
        self.add("sleep", ("спи", "режим сна", "усни"), sleep)
        self.add("wake", ("проснись", "включись"), wake)
        self.add("stats", ("как дела у компьютера", "состояние пк", "нагрузка", "как компьютер"), stats)
        self.add("wander_on", ("погуляй", "гуляй по экрану", "можешь гулять", "перейди в другое место"), wander_on)
        self.add("wander_off", ("стой на месте", "не гуляй", "хватит гулять", "не уходи"), wander_off)
        self.add("music_pause", ("пауза", "поставь на паузу", "останови музыку", "выключи музыку",
                                 "выключи песню", "стоп музыка"), music_pause)
        self.add("music_play", ("продолжи музыку", "включи музыку", "сними с паузы", "включи песню",
                                "продолжи"), music_play)
        self.add("music_next", ("следующий трек", "следующая песня", "переключи трек", "переключи песню",
                                "другую песню", "следующую"), music_next)
        self.add("music_prev", ("предыдущий трек", "предыдущая песня", "верни песню", "прошлый трек"), music_prev)
        self.add("music_what", ("что играет", "что за песня", "какая песня", "что за трек", "что сейчас играет"),
                 music_what)
        self.add("hide", ("спрячься", "скройся", "исчезни", "уйди в фон", "не мешай"), hide)
        self.add("show", ("покажись", "появись", "вернись", "выйди"), show)
        self.add("autohide_off", ("не прячься в играх", "не прячься", "не исчезай", "оставайся на экране"), autohide_off)
        self.add("autohide_on", ("прячься в играх", "прячься в полноэкранных", "уходи в фон в играх"), autohide_on)
        self.add("apps_refresh", ("обнови список программ", "обнови программы", "найди новые программы",
                                  "пересканируй программы"), apps_refresh)
        self.add("apps_count", ("сколько программ ты знаешь", "сколько у меня программ"), apps_count)
        self.add("my_commands", ("какие команды ты помнишь", "мои команды", "список моих команд",
                                 "какие у меня команды", "какие команды ты знаешь"), my_cmds)
        self.add("forget_all_commands", ("забудь все команды", "удали все команды", "забудь все мои команды"),
                 forget_all, danger=True, title="Забыть все твои команды")
        self.add("shutdown", ("отбой", "выключись", "завершай работу"), quit_brain, danger=True,
                 title="Закрыть меня (VERONIKA)")


def startfile_supported():
    return hasattr(os, "startfile")


if not startfile_supported():          # чтобы модуль импортировался и вне Windows
    def _fake_startfile(path):
        subprocess.Popen(["xdg-open", path])
    os.startfile = _fake_startfile     # type: ignore[attr-defined]

