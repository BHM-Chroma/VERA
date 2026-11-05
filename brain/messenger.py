"""Сообщения в мессенджеры «руками» и контакты.

Контакты (contacts.json, обновление их не трогает):
  «запомни контакт Лёша дискорд shadow_77, телеграм lesha_k»
  «какие у меня контакты», «забудь контакт Лёша»
Отправка:
  «напиши Лёше в дискорд привет, как дела»
  «напиши в дискорд shadow_77 привет»            — без контакта, сразу по нику
  «напиши сообщение в дискорд пользователю Лёша сообщение привет»
Discord — приложение на ПК: Ctrl+K → ник → Enter → проверяем заголовок окна → вставляем текст → Enter.
Telegram и WhatsApp (в браузере) — следующий шаг.
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import time

from rapidfuzz import fuzz

from paths import path

IS_WINDOWS = sys.platform == "win32"
FILE = path("contacts.json")

NAMES = {"discord": "Discord", "telegram": "Telegram", "whatsapp": "WhatsApp"}
# «дискорд», «дискорде», «дискорду», «телеграме», «телеге», «ватсапе», «тг»
MSG_WORD = (r"(?:дискорд\w*|discord|телеграмм?\w*|telegram|тг|телег\w*|"
            r"ват?сапп?\w*|вотсап\w*|вацап\w*|whatsapp)")
MSG_PHRASE_RE = re.compile(r"(?:^|\s)(?:(?:в|во|через|по)\s+)?(?P<m>" + MSG_WORD + r")(?=$|[\s,.:!?])", re.I)

SEND_RE = re.compile(r"^(?:напиши|отправь|написать|отправить|скинь|черкни)\s+(?P<rest>.+)$", re.I | re.S)
ADD_RE = re.compile(r"^(?:запомни|добавь|сохрани|создай|новый)\s+(?:новый\s+)?контакт\s+(?P<rest>.+)$", re.I | re.S)
DEL_RE = re.compile(r"^(?:забудь|удали|убери|сотри)\s+контакт\s+(?P<name>.+)$", re.I)
LIST_RE = re.compile(r"(?:какие|мои|покажи|список|перечисли)\b.*\bконтакт", re.I)


def messenger_of(word):
    w = word.lower().strip(".,:!?«»")
    if re.fullmatch(r"дискорд\w*|discord", w):
        return "discord"
    if re.fullmatch(r"телеграмм?\w*|telegram|тг|телег\w*", w):
        return "telegram"
    if re.fullmatch(r"ват?сапп?\w*|вотсап\w*|вацап\w*|whatsapp", w):
        return "whatsapp"
    return None


def _norm(s):
    return s.lower().replace("ё", "е").strip(" .,:!?«»\"'")


def name_matches(word, name):
    """«Лёше» ~ «Лёша», «Андрею» ~ «Андрей», «shadow» ~ «Shadow»."""
    a, b = _norm(word), _norm(name)
    if not a or not b:
        return False
    if a == b:
        return True
    common = len(os.path.commonprefix([a, b]))
    if len(b) >= 3 and common >= max(3, len(b) - 2) and abs(len(a) - len(b)) <= 3:
        return True
    return len(b) >= 5 and fuzz.ratio(a, b) >= 85


# ---------- контакты ----------

class Contacts:
    def __init__(self):
        self.items = []
        self.titles = {}      # ник Discord -> как чат называется в окне (запоминается после «да»)
        self.load()

    def load(self):
        try:
            with open(FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.items = data.get("contacts", [])
            self.titles = data.get("discord_titles", {})
        except (OSError, ValueError):
            self.items, self.titles = [], {}

    def save(self):
        try:
            with open(FILE, "w", encoding="utf-8") as f:
                json.dump({"contacts": self.items, "discord_titles": self.titles}, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[контакты] не сохранилось: {e!r}")

    def find(self, name):
        for c in self.items:
            if _norm(c["name"]) == _norm(name):
                return c
        for c in self.items:
            words = c["name"].split()
            given = name.split()
            if len(words) == len(given) and all(name_matches(g, w) for g, w in zip(given, words)):
                return c
        return None

    def match_prefix(self, words):
        """Контакт, имя которого — начало списка слов: (контакт, сколько слов заняло) или (None, 0)."""
        best = (None, 0)
        for c in self.items:
            cw = c["name"].split()
            if len(cw) <= len(words) and all(name_matches(w, n) for w, n in zip(words, cw)):
                if len(cw) > best[1]:
                    best = (c, len(cw))
        return best

    def upsert(self, name, accounts):
        c = self.find(name)
        if c is None:
            c = {"name": name}
            self.items.append(c)
        c.update(accounts)
        self.save()
        return c

    def remove(self, name):
        c = self.find(name)
        if c:
            self.items.remove(c)
            self.save()
        return c


def describe(c):
    parts = [f"{NAMES[m]} {c[m]}" for m in ("discord", "telegram", "whatsapp") if c.get(m)]
    return f"{c['name']} — " + (", ".join(parts) if parts else "без аккаунтов")


# ---------- разбор фраз ----------

def parse_add(text):
    """«запомни контакт Лёша дискорд shadow_77, телеграм @lesha_k» -> ("Лёша", {"discord": "shadow_77", …})."""
    m = ADD_RE.match(text.strip(" .!?"))
    if not m:
        return None
    tokens = re.split(r"[\s,;]+", m.group("rest").replace(":", " ").strip())
    name, accounts, cur = [], {}, None
    for t in tokens:
        if not t or t.lower() in ("и", "а", "ник", "номер", "логин", "в", "во"):
            continue
        mm = messenger_of(t)
        if mm and (cur is None or accounts.get(cur)):
            cur = mm
            accounts.setdefault(cur, "")
            continue
        if cur is None:
            name.append(t)
        elif cur == "whatsapp" and re.fullmatch(r"[+\d()\-]+", t):
            accounts[cur] += t              # номер мог прийти кусками: «+7 701 123 45 67»
        elif not accounts.get(cur):
            accounts[cur] = t.lstrip("@")
    name = " ".join(name).strip(" -—")
    if cur and accounts.get("whatsapp"):
        accounts["whatsapp"] = re.sub(r"[^\d+]", "", accounts["whatsapp"])
    accounts = {k: v for k, v in accounts.items() if v}
    return name, accounts


def parse_send(text, contacts):
    """Разбор «напиши …». Возвращает dict(messenger, contact, target, text) или None (не сообщение).
    target — ник/номер, по которому искать; contact — найденный контакт (или None)."""
    m = SEND_RE.match(text.strip())
    if not m:
        return None
    rest = m.group("rest").strip()
    mm = MSG_PHRASE_RE.search(rest)
    messenger = messenger_of(mm.group("m")) if mm else None
    if mm:
        rest = (rest[:mm.start()] + " " + rest[mm.end():]).strip()
    rest = re.sub(r"^(?:сообщение|сообщения|смс|текст)\s+", "", rest, flags=re.I)
    rest = re.sub(r"^(?:пользователю|контакту|другу|подруге|юзеру|человеку)\s+", "", rest, flags=re.I)
    # явный разделитель: «… пользователю Лёша сообщение привет», «Лёше: привет»
    who, body = None, None
    sep = re.search(r"\s+(?:сообщение|текст)\s+|\s*:\s*", rest, flags=re.I)
    words = rest.split()
    contact, used = contacts.match_prefix(words)
    if contact:
        who = " ".join(words[:used])
        body = " ".join(words[used:])
    elif sep and sep.start() > 0:
        who, body = rest[:sep.start()], rest[sep.end():]
    elif messenger and words:
        who, body = words[0], " ".join(words[1:])
    if contact is None and who:
        contact = contacts.find(who)
    if not messenger and not contact:
        return None            # «напиши стих про кота» — это не сообщение, это к ChatGPT
    body = re.sub(r"^(?:сообщение|текст|что)\s+", "", (body or "").strip(), flags=re.I).strip(" :,")
    if contact and not messenger:
        have = [k for k in ("discord", "telegram", "whatsapp") if contact.get(k)]
        messenger = have[0] if len(have) == 1 else None
    target = (contact or {}).get(messenger or "", "") if contact else (who or "").lstrip("@")
    return {"messenger": messenger, "contact": contact, "who": who or "", "target": target, "text": body}


# ---------- клавиатура, буфер обмена, окна ----------

def _user32():
    return ctypes.windll.user32


def _clip_get():
    u, k = _user32(), ctypes.windll.kernel32
    u.GetClipboardData.restype = ctypes.c_void_p
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u.OpenClipboard(None):
        return None
    try:
        h = u.GetClipboardData(13)          # CF_UNICODETEXT
        if not h:
            return None
        p = k.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else None
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def _clip_set(text):
    u, k = _user32(), ctypes.windll.kernel32
    k.GlobalAlloc.restype = ctypes.c_void_p
    k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    data = (text or "").encode("utf-16-le") + b"\x00\x00"
    for _ in range(10):
        if u.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise OSError("буфер обмена занят")
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(0x0002, len(data))   # GMEM_MOVEABLE
        p = k.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        u.SetClipboardData(13, h)
    finally:
        u.CloseClipboard()


def _keys(combo):
    import keyboard
    keyboard.send(combo)


def _title(hwnd):
    u = _user32()
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _windows_of(exe_prefix):
    import windows_info as wi
    u = _user32()
    out = []
    for hwnd in wi._top_level_windows():
        if wi._process_name(hwnd).startswith(exe_prefix) and u.GetWindowTextLengthW(hwnd) > 0:
            r = wi._rect(hwnd)
            out.append((hwnd, (r.right - r.left) * (r.bottom - r.top), bool(u.IsWindowVisible(hwnd))))
    out.sort(key=lambda x: (not x[2], -x[1]))
    return [h for h, _, _ in out]


def _foreground(hwnd):
    u = _user32()
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)               # SW_RESTORE
    elif not u.IsWindowVisible(hwnd):
        u.ShowWindow(hwnd, 5)               # SW_SHOW (свёрнут в трей)
    u.keybd_event(0x12, 0, 0, 0)            # Alt — иначе Windows не даст вывести окно наверх
    u.keybd_event(0x12, 0, 2, 0)
    u.SetForegroundWindow(hwnd)
    for _ in range(10):
        time.sleep(0.1)
        if u.GetForegroundWindow() == hwnd:
            return True
    return u.GetForegroundWindow() == hwnd


class SendError(Exception):
    """Не получилось отправить — с понятной фразой."""


class NeedApproval(Exception):
    """Чат открыт, но имя в окне не похоже на ник — спросить человека. title — что в окне, chat — имя чата."""

    def __init__(self, title, chat, messenger="discord"):
        super().__init__(title)
        self.title, self.chat, self.messenger = title, chat, messenger


def chat_name(title):
    """«@Mllcw - Discord» -> «Mllcw»; «Discord | @Mllcw» -> «Mllcw»."""
    t = re.sub(r"\s*[-|–]\s*Discord\s*$|^\s*Discord\s*[-|–]\s*", "", title or "").strip()
    return t.lstrip("@").strip() or (title or "").strip()


# ---------- Discord ----------

def _discord_window(timeout=25.0):
    w = _windows_of("discord")
    visible = [h for h in w if _user32().IsWindowVisible(h)]
    if visible:
        return visible[0]
    # закрыт или свёрнут в трей — запуск ещё раз выводит окно
    upd = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Discord", "Update.exe")
    try:
        if os.path.isfile(upd):
            subprocess.Popen([upd, "--processStart", "Discord.exe"], close_fds=True)
        else:
            os.startfile("discord:")        # type: ignore[attr-defined]
    except OSError as e:
        raise SendError(f"Не смогла открыть Discord ({e}).")
    print("[сообщения] открываю Discord")
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.5)
        visible = [h for h in _windows_of("discord") if _user32().IsWindowVisible(h)]
        if visible:
            time.sleep(1.5 if time.time() - t0 < 3 else 4.0)   # после запуска — ещё загрузка
            return visible[0]
    raise SendError("Discord не открылся. Открой его вручную и повтори.")


def _title_has(title, names):
    t = _norm(title)
    for n in names:
        n = _norm(n).lstrip("@")
        if n and (n in t or fuzz.partial_ratio(n, t) >= 85):
            return True
    return False


def send_discord(target, text, names=()):
    """Открыть личку в Discord (Ctrl+K) и отправить text. names — как ещё может называться чат."""
    if not IS_WINDOWS:
        raise SendError("Отправка работает только на Windows.")
    if not target:
        raise SendError("Не знаю ник в Discord.")
    hwnd = _discord_window()
    if not _foreground(hwnd):
        raise SendError("Не смогла переключиться на Discord — открой его и повтори.")
    before = _title(hwnd)
    old_clip = None
    try:
        old_clip = _clip_get()
    except Exception:  # noqa: BLE001
        pass
    try:
        _keys("esc")
        time.sleep(0.15)
        _keys("ctrl+k")                      # быстрый переход
        time.sleep(0.6)
        _clip_set(target)
        _keys("ctrl+v")
        time.sleep(1.4)                      # Discord подбирает варианты
        if _user32().GetForegroundWindow() != hwnd:
            raise SendError("Окно Discord потеряло фокус — ничего не отправила.")
        _keys("enter")
        time.sleep(1.6)
        title = _title(hwnd)
        print(f"[сообщения] Discord: «{before}» -> «{title}» (ищу {target!r})")
        if not _title_has(title, [target, *names]):
            if chat_name(title).lower() in ("discord", "друзья", "friends", ""):
                _keys("esc")
                raise SendError(f"Не нашла в Discord чат с {target} — ничего не отправила.")
            # открылся чат (или этот чат уже был открыт) с другим именем — пусть человек подтвердит
            raise NeedApproval(title, chat_name(title))
        _type_and_send(hwnd, text)
        print(f"[сообщения] Discord: отправлено {target}: {text!r}")
    finally:
        if old_clip is not None:
            try:
                time.sleep(0.2)
                _clip_set(old_clip)
            except Exception:  # noqa: BLE001
                pass


def _type_and_send(hwnd, text):
    if _user32().GetForegroundWindow() != hwnd:
        raise SendError("Окно Discord потеряло фокус — ничего не отправила.")
    _clip_set(text)
    _keys("ctrl+v")
    time.sleep(0.35)
    _keys("enter")
    time.sleep(0.3)


def send_discord_approved(title, text):
    """После «да»: чат уже открыт — проверяем, что это всё ещё он, и отправляем."""
    if not IS_WINDOWS:
        raise SendError("Отправка работает только на Windows.")
    hwnd = _discord_window()
    if not _foreground(hwnd):
        raise SendError("Не смогла переключиться на Discord — ничего не отправила.")
    now = _title(hwnd)
    if now != title:
        raise SendError(f"В Discord уже открыт другой чат («{now}») — ничего не отправила.")
    old_clip = None
    try:
        old_clip = _clip_get()
    except Exception:  # noqa: BLE001
        pass
    try:
        _type_and_send(hwnd, text)
        print(f"[сообщения] Discord: отправлено после подтверждения в «{title}»: {text!r}")
    finally:
        if old_clip is not None:
            try:
                time.sleep(0.2)
                _clip_set(old_clip)
            except Exception:  # noqa: BLE001
                pass


# ---------- Telegram (web.telegram.org/a в браузере) ----------

TG_COMPOSER_ID = "editable-message-text"     # поле «Сообщение» в Telegram Web A


def telegram_url(target):
    t = target.strip().lstrip("@")
    digits = re.sub(r"[^\d]", "", t)
    if re.fullmatch(r"\+?[\d\s()\-]{7,}", t):
        addr = "tg://resolve?phone=" + digits
    else:
        addr = "tg://resolve?domain=" + t
    import urllib.parse
    return "https://web.telegram.org/a/#?tgaddr=" + urllib.parse.quote(addr, safe="")


def _telegram_window():
    for hwnd in _windows_of("chrome") + _windows_of("msedge") + _windows_of("firefox") + _windows_of("opera") \
            + _windows_of("browser") + _windows_of("yandex"):
        if _user32().IsWindowVisible(hwnd) and _title(hwnd).lower().lstrip("(0123456789) ").startswith("telegram"):
            return hwnd
    return None


def _focus_composer(hwnd, wait=12.0):
    """Найти поле «Сообщение» (UI Automation по id элемента) и поставить в него курсор. True — получилось."""
    try:
        import spotify_ui
        uia, U = spotify_ui._automation()
    except Exception as e:  # noqa: BLE001
        print(f"[сообщения] UI Automation недоступна: {e!r}")
        return False
    cond = uia.CreatePropertyCondition(U.UIA_AutomationIdPropertyId, TG_COMPOSER_ID)
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            el = uia.ElementFromHandle(hwnd).FindFirst(U.TreeScope_Descendants, cond)
            if el:
                el.SetFocus()
                time.sleep(0.2)
                return True
        except Exception:  # noqa: BLE001 — страница ещё грузится
            pass
        time.sleep(0.5)
    return False


def open_telegram_chat(target):
    """Открыть чат по нику/номеру в Telegram Web. Возвращает окно браузера."""
    url = telegram_url(target)
    hwnd = _telegram_window()
    old_clip = None
    try:
        old_clip = _clip_get()
    except Exception:  # noqa: BLE001
        pass
    try:
        if hwnd and _foreground(hwnd):
            # та же вкладка: адрес -> Enter -> F5 (ссылку tgaddr Telegram читает при загрузке страницы)
            _keys("ctrl+l")
            time.sleep(0.2)
            _clip_set(url)
            _keys("ctrl+v")
            time.sleep(0.15)
            _keys("enter")
            time.sleep(0.5)
            _keys("f5")
            print(f"[сообщения] Telegram: открываю {target} в той же вкладке")
        else:
            import webbrowser
            webbrowser.open(url)
            print(f"[сообщения] Telegram: открываю {target} в новой вкладке")
            t0 = time.time()
            while time.time() - t0 < 25:
                time.sleep(0.5)
                hwnd = _telegram_window()
                if hwnd:
                    break
            if not hwnd:
                raise SendError("Не дождалась Telegram в браузере. Открой web.telegram.org и войди в аккаунт.")
            _foreground(hwnd)
    finally:
        if old_clip is not None:
            try:
                time.sleep(0.2)
                _clip_set(old_clip)
            except Exception:  # noqa: BLE001
                pass
    time.sleep(2.0)
    return hwnd


def _telegram_type(hwnd, text):
    if not _foreground(hwnd):
        raise SendError("Не смогла переключиться на Telegram — ничего не отправила.")
    if not _focus_composer(hwnd):
        raise SendError("Не нашла в Telegram поле для сообщения (чат не открылся?) — ничего не отправила.")
    old_clip = None
    try:
        old_clip = _clip_get()
    except Exception:  # noqa: BLE001
        pass
    try:
        _type_and_send(hwnd, text)
    finally:
        if old_clip is not None:
            try:
                time.sleep(0.2)
                _clip_set(old_clip)
            except Exception:  # noqa: BLE001
                pass


def send_telegram(target, text, verified=False):
    if not IS_WINDOWS:
        raise SendError("Отправка работает только на Windows.")
    if not target:
        raise SendError("Не знаю ник в Telegram.")
    hwnd = open_telegram_chat(target)
    if not _focus_composer(hwnd):
        raise SendError(f"Telegram не открыл чат с {target} (ник верный? вход в web.telegram.org выполнен?) — "
                        "ничего не отправила.")
    if not verified:
        # заголовок вкладки всегда «Telegram» — проверить чат по нему нельзя, поэтому в первый раз спрашиваем
        raise NeedApproval(_title(hwnd), "@" + target.lstrip("@"), "telegram")
    _telegram_type(hwnd, text)
    print(f"[сообщения] Telegram: отправлено {target}: {text!r}")


def send_telegram_approved(text):
    hwnd = _telegram_window()
    if not hwnd:
        raise SendError("Вкладка Telegram закрылась — ничего не отправила.")
    _telegram_type(hwnd, text)
    print(f"[сообщения] Telegram: отправлено после подтверждения: {text!r}")


def send(messenger, target, text, names=(), verified=False):
    if messenger == "discord":
        return send_discord(target, text, names)
    if messenger == "telegram":
        return send_telegram(target, text, verified)
    raise SendError(f"{NAMES.get(messenger, messenger)} пока не умею — сначала Discord и Telegram, WhatsApp следующим.")
