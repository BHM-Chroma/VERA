"""Список установленных программ и игр и поиск по нему («Вероника, запусти телегу»).

Откуда берём программы (только Windows):
  • меню «Пуск» целиком, включая приложения Microsoft Store (PowerShell Get-StartApps);
  • ярлыки на рабочем столе — и обычном, и в OneDrive, и общем;
  • игры Steam (steamapps/appmanifest_*.acf во всех библиотеках);
  • игры Epic Games (манифесты лаунчера);
  • «App Paths» из реестра (программы, которые Windows знает по имени exe).
Список строится в фоне при запуске, хранится в apps_index.json и обновляется раз в сутки
или по команде «обнови список программ».
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

from rapidfuzz import fuzz

from paths import BASE_DIR

IS_WINDOWS = sys.platform == "win32"
INDEX_FILE = os.path.join(BASE_DIR, "apps_index.json")
CHOICES_FILE = os.path.join(BASE_DIR, "apps_choices.json")   # что ты выбирал, когда она переспрашивала
REFRESH_SEC = 24 * 3600
NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW: не мигать консолью

# Мусор, который не надо запускать по голосу
JUNK = re.compile(r"uninstall|unins\d*|удал|readme|read me|help|справк|documentation|документац|"
                  r"license|лиценз|website|веб-сайт|release notes|changelog|manual|руководств|"
                  r"repair|восстанов|setup|install|crash|report|debug|safe mode", re.I)

# Как программы называют вслух → как они называются на самом деле
SYNONYMS = {
    "телега": "telegram", "телеграм": "telegram", "телеграмм": "telegram", "тг": "telegram",
    "дискорд": "discord", "дс": "discord",
    "хром": "chrome", "гугл хром": "google chrome", "гугл": "google chrome", "браузер": "chrome",
    "стим": "steam", "эпик": "epic games launcher", "эпик геймс": "epic games launcher",
    "спотифай": "spotify", "спотик": "spotify",
    "ютуб": "youtube", "ватсап": "whatsapp", "вотсап": "whatsapp", "вацап": "whatsapp",
    "вк": "vk", "вконтакте": "vk",
    "блендер": "blender", "годо": "godot", "годот": "godot", "юнити": "unity hub",
    "фотошоп": "photoshop", "иллюстратор": "illustrator", "премьер": "premiere pro",
    "ворд": "word", "эксель": "excel", "поверпоинт": "powerpoint", "пауэрпоинт": "powerpoint",
    "блокнот": "notepad", "пэйнт": "paint", "паинт": "paint", "пейнт": "paint", "калькулятор": "calculator", "проводник": "file explorer",
    "панель управления": "control panel", "параметры": "settings", "настройки": "settings",
    "диспетчер задач": "task manager", "терминал": "terminal", "командная строка": "command prompt",
    "вс код": "visual studio code", "вскод": "visual studio code", "визуал студио": "visual studio",
    "обс": "obs studio", "обс студио": "obs studio", "зум": "zoom", "скайп": "skype",
    "майнкрафт": "minecraft", "доту": "dota 2", "дота": "dota 2", "кс": "counter-strike 2",
    "контра": "counter-strike 2", "кс2": "counter-strike 2", "валорант": "valorant", "лига": "league of legends",
    "геншин": "genshin impact", "хонкай": "honkai star rail", "зензонзеро": "zenless zone zero",
    "зензон": "zenless zone zero", "ззз": "zenless zone zero", "пакет трейсер": "cisco packet tracer",
    "виртуалбокс": "virtualbox", "нвидиа": "nvidia app", "ардуино": "arduino ide",
}

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


def translit(text):
    return "".join(_TRANSLIT.get(c, c) for c in text.lower())


def normalize(name):
    name = name.lower().replace("™", "").replace("®", "").replace("©", "")
    name = re.sub(r"[_\-–—:.,()\[\]{}]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


# ---------- сбор списка ----------

def _known_folder(guid):
    """Путь к «известной папке» Windows (например, рабочий стол — с учётом OneDrive)."""
    import ctypes
    from ctypes import wintypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    u = uuid.UUID(guid)
    g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    p = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) != 0:
        return None
    path = p.value
    ctypes.windll.ole32.CoTaskMemFree(p)
    return path


def _start_apps():
    """Всё меню «Пуск», включая приложения из Microsoft Store."""
    cmd = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
           "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                             capture_output=True, timeout=60, creationflags=NO_WINDOW).stdout
        data = json.loads(out.decode("utf-8", "replace") or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as e:
        print(f"[программы] меню «Пуск» не прочиталось: {e}")
        return []
    if isinstance(data, dict):
        data = [data]
    return [{"name": d["Name"], "kind": "appid", "target": d["AppID"], "source": "Пуск"}
            for d in data if d.get("Name") and d.get("AppID")]


def _shortcuts_in(folder, source):
    res = []
    if not folder or not os.path.isdir(folder):
        return res
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith((".lnk", ".url", ".exe")):
                res.append({"name": os.path.splitext(f)[0], "kind": "file",
                            "target": os.path.join(root, f), "source": source})
        if root.count(os.sep) - folder.count(os.sep) >= 2:
            _dirs.clear()
    return res


def _desktops():
    res = []
    user_desktop = _known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")    # FOLDERID_Desktop (OneDrive тоже)
    public_desktop = _known_folder("C4AA340D-F20F-4863-AFEF-F87EF2E6BA25")  # FOLDERID_PublicDesktop
    for d in {user_desktop, public_desktop, os.path.expanduser(r"~\Desktop")}:
        res += _shortcuts_in(d, "рабочий стол")
    return res


def _reg_value(root, key, name):
    import winreg
    try:
        with winreg.OpenKey(root, key) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _steam_games():
    import winreg
    steam = _reg_value(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath")
    if not steam:
        return []
    libs = {os.path.normpath(steam)}
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, encoding="utf-8", errors="replace") as f:
            for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                libs.add(os.path.normpath(m.group(1).replace("\\\\", "\\")))
    except OSError:
        pass
    games = []
    for lib in libs:
        apps = os.path.join(lib, "steamapps")
        if not os.path.isdir(apps):
            continue
        for fn in os.listdir(apps):
            if not (fn.startswith("appmanifest_") and fn.endswith(".acf")):
                continue
            try:
                with open(os.path.join(apps, fn), encoding="utf-8", errors="replace") as f:
                    txt = f.read()
            except OSError:
                continue
            appid = re.search(r'"appid"\s+"(\d+)"', txt)
            name = re.search(r'"name"\s+"([^"]+)"', txt)
            if appid and name and not re.search(r"redistributable|steamworks|proton", name.group(1), re.I):
                games.append({"name": name.group(1), "kind": "url",
                              "target": f"steam://rungameid/{appid.group(1)}", "source": "Steam"})
    return games


def _epic_games():
    folder = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                          r"Epic\EpicGamesLauncher\Data\Manifests")
    games = []
    if not os.path.isdir(folder):
        return games
    for fn in os.listdir(folder):
        if not fn.endswith(".item"):
            continue
        try:
            with open(os.path.join(folder, fn), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        name, ns, item, app = (d.get("DisplayName"), d.get("CatalogNamespace"),
                               d.get("CatalogItemId"), d.get("AppName"))
        if name and ns and item and app:
            games.append({"name": name, "kind": "url", "source": "Epic Games",
                          "target": f"com.epicgames.launcher://apps/{ns}%3A{item}%3A{app}?action=launch&silent=true"})
    return games


def _app_paths():
    import winreg
    res = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            key = winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\App Paths")
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    exe = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                path = _reg_value(root, r"Software\Microsoft\Windows\CurrentVersion\App Paths\\" + exe, "")
                if path:
                    path = os.path.expandvars(path.strip('"'))
                    if os.path.exists(path):
                        res.append({"name": os.path.splitext(exe)[0], "kind": "file",
                                    "target": path, "source": "реестр"})
    return res


def build_index():
    if not IS_WINDOWS:
        return []
    started = time.time()
    entries = []
    for collect in (_start_apps, _desktops, _steam_games, _epic_games, _app_paths):
        try:
            entries += collect()
        except Exception as e:  # noqa: BLE001 — один источник сломался, остальные работают
            print(f"[программы] {collect.__name__}: {e}")
    seen, result = set(), []
    for e in entries:
        if JUNK.search(e["name"]):
            continue
        key = normalize(e["name"])
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(e)
    print(f"[программы] нашла {len(result)} программ и игр за {time.time() - started:.1f} с")
    return result


# ---------- индекс в памяти ----------

class AppIndex:
    def __init__(self):
        self.entries = []
        self.built_at = 0.0
        self.choices = self._load(CHOICES_FILE, {})
        data = self._load(INDEX_FILE, {})
        if isinstance(data, dict):
            self.entries = data.get("entries", [])
            self.built_at = data.get("built_at", 0.0)
        self._lock = threading.Lock()
        self._building = False

    @staticmethod
    def _load(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return default

    def refresh(self, force=False, wait=False):
        """Пересобрать список в фоне (если устарел или force)."""
        if self._building or (not force and time.time() - self.built_at < REFRESH_SEC and self.entries):
            return
        self._building = True

        def work():
            try:
                entries = build_index()
                if entries:
                    with self._lock:
                        self.entries, self.built_at = entries, time.time()
                    with open(INDEX_FILE, "w", encoding="utf-8") as f:
                        json.dump({"built_at": self.built_at, "entries": entries}, f, ensure_ascii=False)
            finally:
                self._building = False

        t = threading.Thread(target=work, daemon=True)
        t.start()
        if wait:
            t.join()

    def remember_choice(self, query, entry):
        self.choices[normalize(query)] = entry["name"]
        try:
            with open(CHOICES_FILE, "w", encoding="utf-8") as f:
                json.dump(self.choices, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def find(self, query, limit=3):
        """Подходящие программы, лучшие первыми: [(запись, очки), …]."""
        q = normalize(query)
        if not q:
            return []
        with self._lock:
            entries = list(self.entries)
        # уже выбирали для этого запроса — сразу её
        chosen = self.choices.get(q)
        if chosen:
            for e in entries:
                if e["name"] == chosen:
                    return [(e, 100.0)]
        variants = {q, translit(q)}
        if q in SYNONYMS:
            variants.add(normalize(SYNONYMS[q]))
        for word in q.split():   # «открой телегу пожалуйста» → «телега»
            for syn_key, syn in SYNONYMS.items():
                if fuzz.ratio(word, syn_key) >= 80:
                    variants.add(normalize(syn))
        scored = []
        for e in entries:
            name = normalize(e["name"])
            best = 0.0
            for v in variants:
                if v == name:
                    s = 110.0   # точное совпадение важнее похожих («Steam» лучше «Steam VR»)
                elif len(v) <= 3:
                    s = 100.0 if v in name.split() else 0.0   # короткие («vk», «кс») — только целым словом
                else:
                    s = max(fuzz.WRatio(v, name), fuzz.token_set_ratio(v, name) - 3)
                    if name.startswith(v):
                        s += 4
                best = max(best, s)
            if best >= 75:
                scored.append((e, best))
        scored.sort(key=lambda x: (-x[1], len(x[0]["name"])))
        return scored[:limit]


def launch(entry):
    kind, target = entry["kind"], entry["target"]
    if kind == "appid":
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"], creationflags=NO_WINDOW)
    else:
        os.startfile(target)  # ярлык, exe или steam://, com.epicgames.launcher://
