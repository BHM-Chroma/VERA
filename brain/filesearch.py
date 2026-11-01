"""Поиск файлов и папок на ПК по названию.

1) Индекс поиска Windows (тот же, что в «Пуске» и Проводнике) — мгновенно, но знает только
   проиндексированные места (обычно папки пользователя).
2) Если индекс не ответил или ничего не нашёл — сами обходим папки пользователя и другие диски,
   пропуская системные папки и файлы игр, с ограничением по времени.

Фразы разбирает parse_request(): «найди файл отчёт», «открой документ диплом», «где лежит фото кот».
"""
import os
import re
import string
import subprocess
import sys
import time

from rapidfuzz import fuzz

IS_WINDOWS = sys.platform == "win32"

KINDS = {
    "file": None,
    "folder": "folder",
    "doc": {".doc", ".docx", ".odt", ".rtf", ".txt", ".pdf", ".md"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff", ".kra", ".psd"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv"},
    "audio": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"},
    "sheet": {".xls", ".xlsx", ".ods", ".csv"},
    "slides": {".ppt", ".pptx", ".odp", ".key"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "model": {".blend", ".fbx", ".obj", ".glb", ".gltf", ".stl"},
}
KIND_WORDS = [
    (r"папк\w*", "folder"), (r"файл\w*", "file"), (r"документ\w*|текст\w*|pdf|пдф", "doc"),
    (r"фото\w*|фотк\w*|картинк\w*|изображени\w*|скрин\w*|рисун\w*", "image"),
    (r"видео\w*|видос\w*|ролик\w*|запис\w*", "video"), (r"аудио|звук\w*|музык\w* файл", "audio"),
    (r"таблиц\w*|эксел\w*|excel", "sheet"), (r"презентаци\w*", "slides"), (r"архив\w*", "archive"),
    (r"модел\w*|блендер\w*", "model"),
]
KIND_NAMES = {"folder": "папку", "doc": "документ", "image": "картинку", "video": "видео", "audio": "аудио",
              "sheet": "таблицу", "slides": "презентацию", "archive": "архив", "model": "модель"}

SKIP_DIRS = {"windows", "program files", "program files (x86)", "programdata", "appdata", "$recycle.bin",
             "system volume information", "node_modules", ".git", "__pycache__", "venv", ".venv", "steamapps",
             "steamlibrary", "epic games", "xboxgames", "windowsapps", "recovery", "perflogs", "msocache",
             "$windows.~bt", "$windows.~ws", "_internal", ".godot", ".gradle", ".cache", "riot games"}

FIND_RE = re.compile(
    r"(?:(?P<open>открой|запусти)|найди|поищи|ищи|покажи|где(?:\s+(?:лежит|находится|у меня))?)"
    r"\s+(?:мне\s+)?(?:мой\s+|мою\s+|моё\s+|мое\s+|мои\s+)?"
    r"(?P<kind>" + "|".join(w for w, _ in KIND_WORDS) + r")"
    r"(?:\s+(?:под названием|с названием|называется|который называется|которая называется))?\s+(?P<q>.+)$")


def parse_request(text):
    """«найди файл отчёт» -> {"open": False, "kind": "file", "q": "отчёт"}; None — это не поиск файла."""
    t = text.lower().strip(" .!?")
    m = FIND_RE.search(t)
    if not m:
        return None
    kind = next(k for w, k in KIND_WORDS if re.fullmatch(w, m.group("kind")))
    q = m.group("q").strip(" .,!?«»\"")
    q = re.sub(r"\s+(?:на|в)\s+(?:компьютере|пк|компе|диске)$", "", q)
    if len(q) < 2:
        return None
    return {"open": bool(m.group("open")), "kind": kind, "q": q}


# ---------- поиск ----------

def _variants(q):
    from apps import translit
    out = [q]
    lat = translit(q)
    if lat != q:
        out.append(lat)
    return out


def _ok_kind(path, is_dir, kind):
    want = KINDS.get(kind)
    if want is None:
        return True
    if want == "folder":
        return is_dir
    return not is_dir and os.path.splitext(path)[1].lower() in want


def _score(q, name, is_dir):
    stem = name if is_dir else os.path.splitext(name)[0]
    stem = re.sub(r"[_\-.]+", " ", stem.lower())
    return max(fuzz.token_set_ratio(q, stem), fuzz.partial_ratio(q, stem) - 5, fuzz.ratio(q, stem))


def _index_search(words_variants, limit=60):
    """Индекс поиска Windows через OLE DB (Search.CollatorDSO)."""
    if not IS_WINDOWS:
        return None
    def create(progid):
        try:
            import win32com.client          # pywin32 (ставится вместе с wmi)
            return win32com.client.Dispatch(progid)
        except ImportError:
            import comtypes.client          # без генерации обёрток — так работает и в собранном .exe
            return comtypes.client.CreateObject(progid, dynamic=True)

    try:
        conn = create("ADODB.Connection")
        conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
    except Exception as e:  # noqa: BLE001
        print(f"[файлы] индекс Windows недоступен: {e!r}")
        return None
    found = []
    try:
        for words in words_variants:
            cond = " AND ".join("System.FileName LIKE '%" + w.replace("'", "''").replace("%", "") + "%'"
                                for w in words if w)
            if not cond:
                continue
            # System.ItemUrl — настоящий путь («file:C:/Users/…/Desktop/…»); ItemPathDisplay — только подпись
            # Проводника («C:\\Пользователи\\…\\Рабочий стол»), по ней файл не открыть
            sql = (f"SELECT TOP {limit} System.ItemUrl, System.ItemName, System.ItemType "
                   f"FROM SystemIndex WHERE {cond} AND SCOPE='file:'")
            rs = create("ADODB.Recordset")
            rs.Open(sql, conn)
            while not rs.EOF:
                path = _url_to_path(rs.Fields.Item(0).Value)
                if path and os.path.exists(path):
                    item_type = (rs.Fields.Item(2).Value or "").lower()
                    found.append((path, item_type in ("directory", "folder") or os.path.isdir(path)))
                rs.MoveNext()
            rs.Close()
    except Exception as e:  # noqa: BLE001
        print(f"[файлы] ошибка запроса к индексу: {e!r}")
    finally:
        try:
            conn.Close()
        except Exception:  # noqa: BLE001
            pass
    return found


def _url_to_path(url):
    """«file:C:/Users/Aliba/OneDrive/Desktop/arts blend» -> «C:\\Users\\Aliba\\OneDrive\\Desktop\\arts blend»."""
    if not url:
        return ""
    from urllib.parse import unquote
    p = url[5:] if url.lower().startswith("file:") else url
    p = p.lstrip("/") if re.match(r"^/+[A-Za-z]:", p) else p
    if "%" in p:
        p = unquote(p)
    return os.path.normpath(p.replace("/", os.sep))


RU_FOLDERS = {"desktop": "Рабочий стол", "documents": "Документы", "downloads": "Загрузки",
              "pictures": "Изображения", "videos": "Видео", "music": "Музыка"}


def _folder_name(path):
    """Как папку зовут в Проводнике: Desktop -> «Рабочий стол»."""
    parent = os.path.basename(os.path.dirname(path.rstrip("\\/"))) or os.path.dirname(path)
    return RU_FOLDERS.get(parent.lower(), parent)


def _roots():
    home = os.path.expanduser("~")
    roots = []
    for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music", "OneDrive"):
        p = os.path.join(home, sub)
        if os.path.isdir(p):
            roots.append(p)
    if os.path.isdir(home):
        roots.append(home)
    if IS_WINDOWS:
        sysdrive = os.environ.get("SystemDrive", "C:").upper()
        for letter in string.ascii_uppercase:
            d = f"{letter}:\\"
            if f"{letter}:" != sysdrive and os.path.isdir(d):
                roots.append(d)
    return roots


def _scan_search(words_variants, kind, budget=6.0, roots=None):
    """Обход папок с ограничением по времени. Возвращает [(путь, папка ли)]."""
    t0 = time.time()
    seen, found = set(), []
    for root in roots or _roots():
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() - t0 > budget:
                print(f"[файлы] обход остановлен по времени ({budget} с)")
                return found
            if dirpath in seen:
                dirnames[:] = []
                continue
            seen.add(dirpath)
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            low = [(n, n.lower(), False) for n in filenames] + [(n, n.lower(), True) for n in dirnames]
            for name, lname, is_dir in low:
                for words in words_variants:
                    if all(w in lname for w in words):
                        found.append((os.path.join(dirpath, name), is_dir))
                        break
    return found


def search(query, kind="file", limit=10):
    """Лучшие совпадения: [{path, name, is_dir, score, folder}]."""
    variants = _variants(query.lower())
    words_variants = [[w for w in re.split(r"[\s_\-.]+", v) if len(w) >= 2] or [v] for v in variants]
    raw = _index_search(words_variants)
    source = "индекс"
    hits = [(p, d) for p, d in (raw or []) if _ok_kind(p, d, kind)]
    if not hits or not any(max(_score(v, os.path.basename(p), d) for v in variants) >= 70 for p, d in hits):
        # индекс знает не всё (например, другие диски) — дообходим сами
        source = "индекс+обход" if hits else "обход"
        hits += [(p, d) for p, d in _scan_search(words_variants, kind) if _ok_kind(p, d, kind)]
    out, seen = [], set()
    for path, is_dir in hits:
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        name = os.path.basename(path.rstrip("\\/")) or path
        sc = max(_score(v, name, is_dir) for v in variants)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        # чуть выше: недавние и не из временных/скрытых мест
        sc += min(5, max(0, 5 - (time.time() - mtime) / 86400 / 30)) if mtime else 0
        out.append({"path": path, "name": name, "is_dir": is_dir, "score": sc,
                    "folder": _folder_name(path)})
    out.sort(key=lambda x: -x["score"])
    print(f"[файлы] «{query}» ({kind}, {source}): {len(out)} шт., лучшие: "
          f"{[(o['name'], round(o['score'])) for o in out[:3]]}")
    return [o for o in out if o["score"] >= 55][:limit]


# ---------- действия ----------

def show_in_explorer(path):
    """Открыть папку и выделить в ней файл."""
    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])


def open_path(path):
    os.startfile(path)  # type: ignore[attr-defined]
