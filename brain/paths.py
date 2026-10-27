"""Где лежат файлы мозга: рядом со скриптом или рядом с собранным .exe."""
import os
import sys

if getattr(sys, "frozen", False):          # собрано PyInstaller'ом
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def path(*parts):
    return os.path.join(BASE_DIR, *parts)
