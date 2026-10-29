"""Что сейчас играет (Spotify и другие плееры) и управление музыкой.

Как узнаём, что играет:
  1) системные «медиа-сеансы» Windows — то же, что показывает плеер на панели
     громкости (Spotify, Яндекс Музыка, YouTube в браузере и т.д.);
  2) запасной путь для Spotify: заголовок его окна («Исполнитель - Трек»,
     а на паузе — просто «Spotify Premium» / «Spotify Free»).
Управление (пауза, следующий, предыдущий) — мультимедийными клавишами,
как кнопки на клавиатуре: работает с любым плеером.
"""
import ctypes
import sys

IS_WINDOWS = sys.platform == "win32"

try:  # системные медиа-сеансы (пакеты winrt-*); если их нет — только Spotify по заголовку
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _SessionManager,
    )
except Exception:  # noqa: BLE001 — любая проблема с winrt = просто нет этого способа
    _SessionManager = None

_PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
_manager = None
_sessions_broken = False


def _state(playing=False, title="", artist="", app=""):
    return {"playing": bool(playing), "title": title or "", "artist": artist or "", "app": app or ""}


async def _from_sessions():
    """Состояние из медиа-сеансов Windows. None — способ недоступен."""
    global _manager, _sessions_broken
    if _SessionManager is None or _sessions_broken:
        return None
    try:
        if _manager is None:
            _manager = await _SessionManager.request_async()
        sessions = list(_manager.get_sessions())
        if not sessions:
            return _state()
        # сначала играющий Spotify, потом любой играющий, потом текущий сеанс
        def score(s):
            playing = s.get_playback_info().playback_status == _PLAYING
            spotify = "spotify" in (s.source_app_user_model_id or "").lower()
            return (playing, spotify)
        best = max(sessions, key=score)
        playing = best.get_playback_info().playback_status == _PLAYING
        props = await best.try_get_media_properties_async()
        app = (best.source_app_user_model_id or "").split("!")[-1].replace(".exe", "")
        return _state(playing, props.title, props.artist, app)
    except Exception as e:  # noqa: BLE001
        print(f"[музыка] медиа-сеансы Windows недоступны ({e}), смотрю только Spotify")
        _sessions_broken = True
        return None


def _from_spotify_window():
    """Запасной путь: заголовок окна Spotify."""
    if not IS_WINDOWS:
        return _state()
    from ctypes import wintypes
    import windows_info as wi

    user32 = ctypes.windll.user32
    for hwnd in wi._top_level_windows():
        if not wi._process_name(hwnd).startswith("spotify"):
            continue
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            continue
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if title.lower().startswith("spotify") or " - " not in title:
            continue          # «Spotify Premium» = на паузе / ничего не играет
        artist, track = title.split(" - ", 1)
        return _state(True, track, artist, "Spotify")
    return _state()


async def get_state():
    import asyncio
    st = await _from_sessions()
    if st is None or (not st["playing"] and IS_WINDOWS):
        sp = await asyncio.to_thread(_from_spotify_window)
        if sp["playing"] or st is None:
            return sp
    return st


# ---------- управление ----------

_KEYS = {"play_pause": 0xB3, "next": 0xB0, "prev": 0xB1}


def press(action):
    """Нажать мультимедийную клавишу: play_pause, next, prev."""
    if not IS_WINDOWS:
        return False
    vk = _KEYS[action]
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)      # нажать
    user32.keybd_event(vk, 0, 2, 0)      # отпустить (KEYEVENTF_KEYUP)
    return True
