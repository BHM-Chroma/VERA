"""Где на экране окна других программ (для сидения на краю окна).

Возвращает прямоугольник окна в физических пикселях Windows.
Свои окна (персонаж, редактор Godot, консоль мозга), рабочий стол, панель
задач, свёрнутые, развёрнутые и полноэкранные окна не подходят.
"""
import ctypes
import os
import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    kernel32 = ctypes.windll.kernel32

    # Чтобы координаты совпадали с Godot при масштабе экрана 125–150%
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    # точные типы, чтобы 64-битные дескрипторы окон не обрезались
    H = wintypes.HWND
    user32.GetForegroundWindow.restype = H
    for fn in ("IsWindow", "IsWindowVisible", "IsIconic", "IsZoomed", "GetWindowTextLengthW"):
        getattr(user32, fn).argtypes = [H]
    user32.GetClassNameW.argtypes = [H, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [H, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowRect.argtypes = [H, ctypes.POINTER(wintypes.RECT)]
    user32.MonitorFromWindow.argtypes = [H, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    dwmapi.DwmGetWindowAttribute.argtypes = [H, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

SKIP_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow", "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland",
    "XamlExplorerHostIslandWindow", "ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS",
}
SKIP_PROCESSES = ("godot", "veronika", "python", "pythonw", "shellexperiencehost", "searchhost",
                  "startmenuexperiencehost", "textinputhost", "lockapp")

# PID процессов самого персонажа (и мозга): на свои окна садиться нельзя,
# иначе она садится на себя, сдвигает своё окно и «едет» за ним вверх без конца.
OWN_PIDS = {os.getpid()}


def set_own_pid(pid):
    if pid:
        OWN_PIDS.add(int(pid))


def _pid(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value
MIN_W, MIN_H = 300, 150


def _process_name(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def _rect(hwnd):
    r = wintypes.RECT()
    # «настоящая» рамка без невидимых теней Windows 10/11
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def _cloaked(hwnd):
    val = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(val), ctypes.sizeof(val))
    return val.value != 0


def describe(hwnd):
    """Информация об окне или None, если на нём сидеть нельзя."""
    if not IS_WINDOWS or not hwnd or not user32.IsWindow(hwnd):
        return None
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        return None
    if _cloaked(hwnd):
        return None
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    if cls.value in SKIP_CLASSES:
        return None
    if user32.GetWindowTextLengthW(hwnd) == 0:
        return None
    if _pid(hwnd) in OWN_PIDS:
        return None
    proc = _process_name(hwnd)
    if proc.startswith(SKIP_PROCESSES):
        return None
    r = _rect(hwnd)
    w, h = r.right - r.left, r.bottom - r.top
    if w < MIN_W or h < MIN_H:
        return None
    # полноэкранное окно (игра, видео) — не подходит
    mon = user32.MonitorFromWindow(hwnd, 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    m = mi.rcMonitor
    if r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom:
        return None
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    return {"hwnd": int(hwnd or 0), "x": r.left, "y": r.top, "w": w, "h": h,
            "title": title.value, "process": proc}


_last_active = 0


def remember_active():
    """Запомнить активное окно (если подходит). Вызывается раз в полсекунды:
    когда пользователь кликает по персонажу, активным становится он сам,
    а нам нужно окно, в котором человек работал перед этим."""
    global _last_active
    if not IS_WINDOWS:
        return
    hwnd = user32.GetForegroundWindow()
    if hwnd and describe(hwnd):
        _last_active = hwnd


def pick_window():
    """Активное окно, а если активен сам персонаж — последнее активное окно."""
    if not IS_WINDOWS:
        return None
    info = describe(user32.GetForegroundWindow())
    if info:
        return info
    return describe(_last_active) if _last_active else None
