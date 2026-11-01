# Свой хук вместо встроенного в PyInstaller: тот ищет пакет «webrtcvad», а у нас стоит
# «webrtcvad-wheels» (тот же модуль, готовые сборки под Windows) — и встроенный хук падает.
from PyInstaller.utils.hooks import copy_metadata

hiddenimports = ["_webrtcvad"]
datas = []
for dist in ("webrtcvad-wheels", "webrtcvad"):
    try:
        datas = copy_metadata(dist)
        break
    except Exception:  # noqa: BLE001 — метаданных нет, модулю они не нужны
        pass
