"""Spotify «руками»: открыть нужную страницу в приложении Spotify и нажать там Play.

Как человек: Spotify открывается на странице трека / плейлиста / поиска (ссылки spotify:…),
потом Вероника находит на странице кнопку «Play» через специальные возможности Windows
(UI Automation — то же, чем пользуются экранные дикторы) и нажимает её. Если кнопку нажать
«изнутри» не вышло — кликает по ней мышкой и возвращает курсор на место.

Веб-API Spotify для этого не нужен: он лишь помогает точнее найти трек/плейлист, если подключён.
Всё, что видно на странице, пишется в brain.log строками «[spotify-ui] …» — по ним легко понять,
что пошло не так.
"""
import ctypes
import os
import re
import subprocess
import sys
import time
import urllib.parse

from rapidfuzz import fuzz

IS_WINDOWS = sys.platform == "win32"

NOT_PLAY = ("pause", "пауз", "shuffle", "перемеш", "repeat", "повтор")
SHUFFLE_WORDS = ("shuffle", "перемеш")


class UIError(Exception):
    """Не получилось нажать в Spotify — с понятной фразой."""


# ---------- окно Spotify ----------

def _spotify_windows():
    import windows_info as wi
    user32 = ctypes.windll.user32
    out = []
    for hwnd in wi._top_level_windows():
        if not user32.IsWindowVisible(hwnd) or not wi._process_name(hwnd).startswith("spotify"):
            continue
        if user32.GetWindowTextLengthW(hwnd) == 0:
            continue
        r = wi._rect(hwnd)
        out.append((hwnd, (r.right - r.left) * (r.bottom - r.top)))
    out.sort(key=lambda x: -x[1])
    return [h for h, _ in out]


def find_window():
    w = _spotify_windows() if IS_WINDOWS else []
    return w[0] if w else None


def _exe_path():
    for p in (os.path.join(os.environ.get("APPDATA", ""), "Spotify", "Spotify.exe"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "Spotify.exe")):
        if p and os.path.isfile(p):
            return p
    return None


def ensure_running(timeout=20.0):
    """Окно Spotify (запускает приложение, если оно закрыто)."""
    hwnd = find_window()
    if hwnd:
        return hwnd
    exe = _exe_path()
    try:
        if exe:
            # флаг включает «специальные возможности» страницы сразу — кнопки видны с первой секунды
            subprocess.Popen([exe, "--force-renderer-accessibility"], close_fds=True)
        else:
            os.startfile("spotify:")      # type: ignore[attr-defined]
    except OSError as e:
        raise UIError(f"Не смогла запустить Spotify ({e}). Он установлен?")
    print(f"[spotify-ui] запускаю Spotify ({'exe' if exe else 'spotify:'})")
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.5)
        hwnd = find_window()
        if hwnd:
            time.sleep(2.5)               # даём приложению загрузиться
            return hwnd
    raise UIError("Spotify не открылся. Запусти его вручную и повтори.")


def open_uri(uri):
    """Открыть страницу в приложении Spotify (spotify:track:…, spotify:search:…)."""
    print(f"[spotify-ui] открываю {uri}")
    os.startfile(uri)                     # type: ignore[attr-defined]


def search_uri(query):
    return "spotify:search:" + urllib.parse.quote(query)


# ---------- UI Automation ----------

_uia = None
_UIA = None


def _automation():
    global _uia, _UIA
    if _uia is None:
        import comtypes.client
        try:
            from comtypes.gen import UIAutomationClient as mod      # собрано заранее (в .exe)
        except ImportError:
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as mod
        _UIA = mod
        _uia = comtypes.client.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
    return _uia, _UIA


def _buttons(hwnd):
    """[(имя, (лево, верх, право, низ), элемент)] — все видимые кнопки в окне."""
    uia, U = _automation()
    root = uia.ElementFromHandle(hwnd)
    cond = uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_ButtonControlTypeId)
    arr = root.FindAll(U.TreeScope_Descendants, cond)
    out = []
    for i in range(arr.Length):
        e = arr.GetElement(i)
        try:
            name = (e.CurrentName or "").strip()
            if not name or e.CurrentIsOffscreen:
                continue
            r = e.CurrentBoundingRectangle
            if r.right - r.left < 4 or r.bottom - r.top < 4:
                continue
            out.append((name, (r.left, r.top, r.right, r.bottom), e))
        except Exception:  # noqa: BLE001 — элемент исчез, пока читали
            continue
    return out


PLAY_RE = re.compile(r"^(?:play|воспроизв\w*|слушать|включить|играть|запустить)\b", re.IGNORECASE)


def _is_play(name):
    low = name.lower()
    return bool(PLAY_RE.match(low)) and not any(w in low for w in NOT_PLAY)


def _strip_play(name):
    low = name.lower()
    low = PLAY_RE.sub("", low, count=1)
    return re.sub(r"[«»\"“”]", "", low).strip()


def _window_rect(hwnd):
    import windows_info as wi
    r = wi._rect(hwnd)
    return r.left, r.top, r.right, r.bottom


def _content_buttons(hwnd, wait=8.0):
    """Кнопки «Play» на странице (без панели плеера внизу). Ждёт, пока страница прогрузится."""
    t0, buttons = time.time(), []
    while time.time() - t0 < wait:
        try:
            buttons = _buttons(hwnd)
        except Exception as e:  # noqa: BLE001
            print(f"[spotify-ui] не читается окно Spotify: {e!r}")
            buttons = []
        left, top, right, bottom = _window_rect(hwnd)
        bar = bottom - max(100, int(0.12 * (bottom - top)))      # панель плеера
        plays = [b for b in buttons if _is_play(b[0]) and (b[1][1] + b[1][3]) / 2 < bar]
        if plays:
            return plays, buttons
        time.sleep(0.6)
    return [], buttons


def _log_buttons(buttons, plays):
    names = [b[0] for b in buttons]
    print(f"[spotify-ui] кнопок в окне: {len(names)}; «играть»: {[p[0][:50] for p in plays[:12]]}")
    if not plays:
        print(f"[spotify-ui] все кнопки: {[n[:40] for n in names[:60]]}")


def _foreground(hwnd):
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)        # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)     # Alt: Windows разрешает перевести окно наверх
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)


def _press(hwnd, button):
    """Нажать кнопку: сначала «изнутри» (Invoke), не вышло — кликнуть мышкой."""
    name, (l, t, r, b), el = button
    _uia_, U = _automation()
    try:
        pat = el.GetCurrentPattern(U.UIA_InvokePatternId)
        if pat:
            pat.QueryInterface(U.IUIAutomationInvokePattern).Invoke()
            print(f"[spotify-ui] нажала «{name[:60]}» (invoke)")
            return
    except Exception as e:  # noqa: BLE001
        print(f"[spotify-ui] invoke не сработал: {e!r} — кликаю мышкой")
    user32 = ctypes.windll.user32
    _foreground(hwnd)

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    old = POINT()
    user32.GetCursorPos(ctypes.byref(old))
    user32.SetCursorPos((l + r) // 2, (t + b) // 2)
    time.sleep(0.08)
    user32.mouse_event(0x0002, 0, 0, 0, 0)    # левая вниз
    user32.mouse_event(0x0004, 0, 0, 0, 0)    # левая вверх
    time.sleep(0.08)
    user32.SetCursorPos(old.x, old.y)
    print(f"[spotify-ui] кликнула «{name[:60]}» мышкой в ({(l + r) // 2}, {(t + b) // 2})")


# ---------- действия ----------

def play_page(uri):
    """Открыть страницу (трек, плейлист, альбом, «Любимые треки») и нажать большую кнопку Play."""
    hwnd = ensure_running()
    open_uri(uri)
    time.sleep(1.5)
    hwnd = find_window() or hwnd
    plays, buttons = _content_buttons(hwnd)
    _log_buttons(buttons, plays)
    if not plays:
        raise UIError("Открыла Spotify, но не нашла кнопку Play на странице.")
    # большая зелёная кнопка в шапке страницы — самая крупная из «играть»
    best = max(plays, key=lambda b: ((b[1][2] - b[1][0]) * (b[1][3] - b[1][1]), -b[1][1]))
    _press(hwnd, best)


def play_search(query, prefer=""):
    """Поиск в Spotify и Play у результата, больше всего похожего на запрос (prefer — точное название)."""
    hwnd = ensure_running()
    open_uri(search_uri(query))
    time.sleep(1.8)
    hwnd = find_window() or hwnd
    plays, buttons = _content_buttons(hwnd)
    _log_buttons(buttons, plays)
    if not plays:
        raise UIError(f"Открыла поиск Spotify «{query}», но не нашла кнопку Play.")
    want = (prefer or query).lower()

    def score(b):
        text = _strip_play(b[0])
        s = max(fuzz.token_set_ratio(want, text), fuzz.partial_ratio(want, text))
        return s - b[1][1] / 10000          # при равенстве — то, что выше на странице
    best = max(plays, key=score)
    print(f"[spotify-ui] лучшая: «{best[0][:60]}» ({score(best):.0f})")
    if score(best) < 45:
        raise UIError(f"В поиске Spotify нет ничего похожего на «{query}».")
    _press(hwnd, best)
    return re.sub(r"[«»\"“”]", "", PLAY_RE.sub("", best[0], count=1)).strip()


def set_shuffle(on):
    """Включить/выключить «перемешивание» кнопкой в панели плеера. True — получилось."""
    hwnd = find_window()
    if not hwnd:
        return False
    try:
        buttons = _buttons(hwnd)
    except Exception as e:  # noqa: BLE001
        print(f"[spotify-ui] перемешивание: окно не читается {e!r}")
        return False
    sh = [b for b in buttons if any(w in b[0].lower() for w in SHUFFLE_WORDS)]
    print(f"[spotify-ui] перемешивание: {[b[0] for b in sh]}")
    if not sh:
        return False
    btn = sh[-1]
    state = None
    try:
        _u, U = _automation()
        pat = btn[2].GetCurrentPattern(U.UIA_TogglePatternId)
        if pat:
            state = pat.QueryInterface(U.IUIAutomationTogglePattern).CurrentToggleState == 1
    except Exception:  # noqa: BLE001
        pass
    if state is None:   # по названию: «Enable shuffle» / «Включить …» = сейчас выключено
        low = btn[0].lower()
        state = not low.startswith(("enable", "turn on", "включ"))
    if state != on:
        _press(hwnd, btn)
    return True


def run_and_verify(action, want, check, norm):
    """action() нажимает Play; потом ждём по данным Windows, что заиграло нужное.
    want — название трека ("" — любое новое). Возвращает (заиграло?, что играет)."""
    before = ("", False)
    try:
        on, title = check()
        before = (title, on)
    except Exception:  # noqa: BLE001
        pass
    result = action()
    w = norm(want or "")

    def ok(on, title):
        if not on:
            return False
        if w:
            t = norm(title)
            return bool(t) and (t == w or w in t or t in w)
        return not before[1] or norm(title) != norm(before[0])
    title, on = "", False
    for _ in range(12):
        time.sleep(0.5)
        try:
            on, title = check()
        except Exception as e:  # noqa: BLE001
            print(f"[spotify-ui] проверка через Windows не удалась: {e!r} — считаю, что включилось")
            return True, result
        if ok(on, title):
            print(f"[spotify-ui] играет: {title!r}")
            return True, title
    print(f"[spotify-ui] не заиграло (сейчас: {title!r}, играет={on})")
    return False, title
