"""Spotify: найти трек по названию/исполнителю и включить его (нужен Spotify Premium).

Подключение один раз: «Вероника, подключи Spotify» → вставить Client ID приложения из
developer.spotify.com (Redirect URI: http://127.0.0.1:8766/callback) → в браузере нажать «Принять».
Вход — по схеме PKCE: секретный ключ приложения не нужен, пароль от Spotify сюда не попадает.
Токены хранятся в spotify.json рядом с мозгом (обновление их не трогает).
"""
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import socketserver
import threading
import time
import urllib.parse
import webbrowser

import requests
from rapidfuzz import fuzz

from paths import path

FILE = path("spotify.json")
PORT = 8766
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
SCOPES = "user-modify-playback-state user-read-playback-state"
API = "https://api.spotify.com/v1"
CLIENT_ID_RE = re.compile(r"\b[0-9a-fA-F]{32}\b")


class SpotifyError(Exception):
    """Ошибка с готовой фразой для облачка."""


def _why(r):
    """Текст ошибки от самого Spotify (для лога и подсказки)."""
    try:
        e = r.json().get("error", {})
        return (e.get("message") or e.get("reason") or "").strip() if isinstance(e, dict) else str(e)
    except ValueError:
        return (r.text or "")[:200].strip()


class Spotify:
    def __init__(self):
        self.data = {}
        self.load()

    # ---------- хранение ----------

    def load(self):
        try:
            with open(FILE, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def save(self):
        try:
            with open(FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except OSError:
            pass

    @property
    def client_id(self):
        return self.data.get("client_id", "")

    @property
    def connected(self):
        return bool(self.client_id and self.data.get("refresh_token"))

    def set_client_id(self, cid):
        self.data = {"client_id": cid.strip().lower()}
        self.save()

    def forget(self):
        self.data = {"client_id": self.client_id} if self.client_id else {}
        self.save()

    # ---------- вход (PKCE) ----------

    def authorize(self, timeout=300):
        """Открывает страницу Spotify и ждёт, пока человек нажмёт «Принять». Блокирует (до timeout сек)."""
        if not self.client_id:
            raise SpotifyError("Сначала нужен Client ID.")
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(16)
        result = {}
        done = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if not self.path.startswith("/callback"):
                    self.send_response(404)
                    self.end_headers()
                    return
                ok = q.get("state", [""])[0] == state and "code" in q
                result["code"] = q.get("code", [""])[0] if ok else ""
                result["error"] = q.get("error", [""])[0] if not ok else ""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                msg = ("Готово! Spotify подключён к VERONIKA. Вкладку можно закрыть." if ok
                       else "Не получилось подключить Spotify. Вкладку можно закрыть и попробовать ещё раз.")
                self.wfile.write(f"<html><body style='font-family:sans-serif;background:#1b0d22;color:#ffd6ec;"
                                 f"display:flex;align-items:center;justify-content:center;height:90vh'>"
                                 f"<h2>{msg}</h2></body></html>".encode("utf-8"))
                done.set()

            def log_message(self, *args):
                pass

        try:
            server = socketserver.TCPServer(("127.0.0.1", PORT), Handler)
        except OSError:
            raise SpotifyError(f"Порт {PORT} занят — не могу дождаться ответа Spotify.")
        server.timeout = 0.5
        url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": REDIRECT,
            "code_challenge_method": "S256", "code_challenge": challenge, "scope": SCOPES, "state": state,
        })
        webbrowser.open(url)
        deadline = time.time() + timeout
        try:
            while not done.is_set() and time.time() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if not result.get("code"):
            if result.get("error") == "access_denied":
                raise SpotifyError("Ты не разрешил доступ — Spotify не подключён.")
            if not done.is_set():
                raise SpotifyError("Не дождалась ответа от Spotify. Скажи «подключи Spotify» ещё раз.")
            raise SpotifyError("Spotify не пустил. Проверь Redirect URI и что твоя почта есть в User Management.")
        r = requests.post("https://accounts.spotify.com/api/token", data={
            "grant_type": "authorization_code", "code": result["code"], "redirect_uri": REDIRECT,
            "client_id": self.client_id, "code_verifier": verifier,
        }, timeout=15)
        if r.status_code != 200:
            raise SpotifyError(f"Spotify не выдал доступ ({r.status_code}). Проверь Client ID и Redirect URI.")
        self._store_tokens(r.json())

    def _store_tokens(self, tok):
        self.data["access_token"] = tok["access_token"]
        self.data["expires"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        if tok.get("refresh_token"):
            self.data["refresh_token"] = tok["refresh_token"]
        self.save()

    def _token(self):
        if not self.connected:
            raise SpotifyError("Spotify ещё не подключён. Скажи «подключи Spotify».")
        if time.time() < self.data.get("expires", 0) and self.data.get("access_token"):
            return self.data["access_token"]
        r = requests.post("https://accounts.spotify.com/api/token", data={
            "grant_type": "refresh_token", "refresh_token": self.data["refresh_token"], "client_id": self.client_id,
        }, timeout=15)
        if r.status_code != 200:
            self.forget()
            raise SpotifyError("Доступ к Spotify истёк. Скажи «подключи Spotify» ещё раз.")
        self._store_tokens(r.json())
        return self.data["access_token"]

    def _api(self, method, url, **kw):
        try:
            r = requests.request(method, API + url, headers={"Authorization": f"Bearer {self._token()}"},
                                 timeout=15, **kw)
        except requests.RequestException:
            raise SpotifyError("Не достучалась до Spotify — нет интернета?")
        if r.status_code == 401:            # токен протух раньше срока
            self.data["expires"] = 0
            r = requests.request(method, API + url, headers={"Authorization": f"Bearer {self._token()}"},
                                 timeout=15, **kw)
        return r

    # ---------- поиск ----------

    def search(self, query):
        """Лучший трек по запросу или None. Возвращает dict(uri, name, artists, score)."""
        from apps import translit
        tries = [query]
        lat = translit(query)
        if lat != query:
            tries.append(lat)                 # «линкин парк намб» -> «linkin park namb»
        best = None
        for q in tries:
            params = {"q": q, "type": "track", "limit": 10}
            if not self.data.get("no_market"):
                params["market"] = "from_token"
            r = self._api("GET", "/search", params=params)
            if r.status_code == 403 and "market" in params:
                print(f"[spotify] поиск 403 с market: {_why(r)!r} — пробую без страны")
                params.pop("market")
                r = self._api("GET", "/search", params=params)
                if r.status_code == 200:
                    self.data["no_market"] = True
                    self.save()
            if r.status_code == 403:
                why = _why(r)
                print(f"[spotify] поиск 403: {why!r}")
                raise SpotifyError(f"Spotify не пускает ({why or '403'}). Скажи «подключи Spotify» заново; "
                                   "если не поможет — проверь, что в User Management та же почта, что в аккаунте Spotify.")
            if r.status_code != 200:
                print(f"[spotify] поиск {r.status_code}: {_why(r)!r}")
                raise SpotifyError(f"Spotify ответил ошибкой ({r.status_code}: {_why(r)}).")
            items = r.json().get("tracks", {}).get("items", [])
            for rank, t in enumerate(items):
                artists = ", ".join(a["name"] for a in t.get("artists", []))
                full = f"{t['name']} {artists}".lower()
                score = max(fuzz.token_set_ratio(q, full), fuzz.token_set_ratio(lat, translit(full)),
                            fuzz.partial_ratio(q, t["name"].lower()))
                score -= rank * 1.5             # порядок Spotify тоже важен
                if best is None or score > best["score"]:
                    best = {"uri": t["uri"], "name": t["name"], "artists": artists, "score": score}
            if best and best["score"] >= 85:
                break
        return best if best and best["score"] >= 55 else None

    # ---------- воспроизведение ----------

    def _devices(self):
        r = self._api("GET", "/me/player/devices")
        return r.json().get("devices", []) if r.status_code == 200 else []

    def _this_pc(self, devices):
        """Устройство «этот компьютер»: тип Computer, лучше всего — с именем ПК."""
        pcs = [d for d in devices if d.get("type") == "Computer"]
        host = (os.environ.get("COMPUTERNAME") or "").lower()
        for d in pcs:
            if host and host in (d.get("name") or "").lower():
                return d
        active = [d for d in pcs if d.get("is_active")]
        return (active or pcs or [None])[0]

    def _wait_devices(self, start_app):
        devices = self._devices()
        if not any(d.get("type") == "Computer" for d in devices) and start_app:
            try:
                os.startfile("spotify:")      # type: ignore[attr-defined]
            except (OSError, AttributeError):
                raise SpotifyError("Spotify не запущен, и я не смогла его открыть.")
            for _ in range(20):
                time.sleep(0.75)
                devices = self._devices()
                if any(d.get("type") == "Computer" for d in devices):
                    break
        return devices

    def _now_playing(self):
        r = self._api("GET", "/me/player")
        if r.status_code != 200:
            return {}
        try:
            return r.json() or {}
        except ValueError:
            return {}

    def play(self, uri, name="", check=None):
        """Включает трек в Spotify на этом компьютере и проверяет, что он заиграл.

        check() -> (играет ли, название) — что реально играет, по данным Windows. Spotify для приложений
        в режиме разработки не сообщает, что сейчас играет, и иногда, приняв команду, ставит трек на паузу,
        поэтому шаги идут по очереди, пока Windows не покажет нужную песню:
          1) команда «играй» на этот ПК;
          2) открыть трек в самом приложении (это «будит» плеер) + снова «играй»;
          3) трек загружен, но на паузе — нажать мультимедийную клавишу Play."""
        devices = self._wait_devices(start_app=True)
        print(f"[spotify] устройства: {[(d.get('name'), d.get('type'), d.get('is_active')) for d in devices]}")
        dev = self._this_pc(devices)
        if dev is None:
            raise SpotifyError("Не вижу Spotify на этом компьютере. Открой приложение Spotify и повтори.")
        if not dev.get("is_active"):
            self._api("PUT", "/me/player", json={"device_ids": [dev["id"]], "play": False})
            time.sleep(0.6)

        want = _norm(name)

        def playing_now(wait):
            """Ждёт до wait секунд, пока заиграет нужная песня. (заиграла?, что сейчас, играет ли)"""
            title, on = "", False
            for _ in range(int(wait / 0.5)):
                time.sleep(0.5)
                if check:
                    try:
                        on, title = check()
                    except Exception as e:  # noqa: BLE001
                        print(f"[spotify] проверка через Windows не удалась: {e}")
                        check_failed[0] = True
                        return True, "", True
                else:
                    st = self._now_playing()
                    on, title = bool(st.get("is_playing")), (st.get("item") or {}).get("name", "")
                if on and want and (_norm(title) == want or want in _norm(title) or _norm(title) in want):
                    return True, title, on
            return False, title, on

        check_failed = [False]

        def send_play():
            r = self._api("PUT", f"/me/player/play?device_id={dev['id']}", json={"uris": [uri]})
            if r.status_code in (200, 202, 204):
                return
            reason = ""
            try:
                reason = r.json().get("error", {}).get("reason", "")
            except (ValueError, AttributeError):
                pass
            print(f"[spotify] play на {dev.get('name')} {r.status_code}: {reason!r} {_why(r)!r}")
            if r.status_code == 403 and reason == "PREMIUM_REQUIRED":
                raise SpotifyError("Spotify разрешает включать треки только с Premium.")
            if r.status_code == 404:
                self._api("PUT", "/me/player", json={"device_ids": [dev["id"]], "play": False})
                time.sleep(0.8)
                r = self._api("PUT", f"/me/player/play?device_id={dev['id']}", json={"uris": [uri]})
                if r.status_code in (200, 202, 204):
                    return
            raise SpotifyError(f"Spotify не смог включить трек ({r.status_code}: {_why(r)}).")

        # 1) просто «играй»
        send_play()
        ok, title, on = playing_now(3.0)
        print(f"[spotify] шаг 1: {'играет' if ok else 'нет'} (сейчас: {title!r}, играет={on})")
        if ok:
            return
        # 2) открыть трек в приложении и снова «играй»
        try:
            os.startfile(uri)              # type: ignore[attr-defined]
            time.sleep(1.2)
        except (OSError, AttributeError):
            pass
        send_play()
        ok, title, on = playing_now(3.5)
        print(f"[spotify] шаг 2: {'играет' if ok else 'нет'} (сейчас: {title!r}, играет={on})")
        if ok:
            return
        # 3) нужная песня загружена, но стоит на паузе — нажимаем Play
        if check and not on:
            from music import press
            press("play_pause")
            ok, title, on = playing_now(2.5)
            print(f"[spotify] шаг 3: {'играет' if ok else 'нет'} (сейчас: {title!r}, играет={on})")
            if ok:
                return
        if on and title:
            raise SpotifyError(f"Spotify вместо этого играет «{title}». Попробуй сказать название точнее.")
        raise SpotifyError("Spotify принял команду, но трек не заиграл. Нажми Play в Spotify один раз и повтори.")


def _norm(text):
    text = (text or "").lower()
    text = re.sub(r"\s*[\(\[].*?[\)\]]", "", text)       # «Numb (Live)» -> «numb»
    text = re.sub(r"\s+-\s+.*$", "", text)                # «In the End - 2020 Remaster» -> «in the end»
    return re.sub(r"[^\w ]+", "", text).strip()


# ---------- разбор фразы ----------

PLAY_RE = re.compile(
    r"(?:включи|поставь|найди|запусти|сыграй|играй|врубай|вруби)\s+(?:мне\s+)?"
    r"(?:песню|трек|композицию|песенку)\s+(?P<q>.+)$")
SPOTIFY_RE = re.compile(
    r"(?:включи|поставь|найди|запусти|сыграй|вруби)\s+(?:мне\s+)?(?P<q>.+?)\s+(?:в|на|из)\s+спот[иы]ф\w*$")
NOT_QUERY = {"на паузу", "дальше", "следующую", "другую", "предыдущую", "заново", "погромче", "потише"}


def parse_request(text):
    """«включи песню numb linkin park» -> «numb linkin park». None — это не просьба найти трек."""
    t = text.lower().strip(" .!?")
    m = PLAY_RE.search(t) or SPOTIFY_RE.search(t)
    if not m:
        return None
    q = m.group("q").strip(" .,!?«»\"")
    q = re.sub(r"^(?:под названием|называется)\s+", "", q)
    q = re.sub(r"\s+(?:от|исполнителя|группы|исполнитель|в исполнении)\s+", " ", q)
    q = re.sub(r"\s+(?:в|на|из)\s+спот[иы]ф\w*$", "", q)
    if len(q) < 2 or q in NOT_QUERY:
        return None
    return q
