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
SCOPES = ("user-modify-playback-state user-read-playback-state "
          "playlist-read-private playlist-read-collaborative user-library-read")
LIBRARY_SCOPES = {"playlist-read-private", "user-library-read"}
API = "https://api.spotify.com/v1"
CLIENT_ID_RE = re.compile(r"\b[0-9a-fA-F]{32}\b")


class SpotifyError(Exception):
    """Ошибка с готовой фразой для облачка."""


class NeedReauth(SpotifyError):
    """Нужно ещё одно разрешение от человека (видеть плейлисты) — снова «Принять» в Spotify."""


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
        if tok.get("scope") is not None:
            self.data["scope"] = tok["scope"]
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
        """Включить один трек."""
        self._start({"uris": [uri]}, name, uri, check)

    def play_context(self, context_uri, check=None, track_uri="", track_name="", shuffle=None):
        """Включить плейлист (с начала, вперемешку или с нужного трека)."""
        body = {"context_uri": context_uri}
        if track_uri:
            body["offset"] = {"uri": track_uri}
        self._start(body, track_name, context_uri, check, shuffle=shuffle)

    def play_uris(self, uris, check=None, name=""):
        """Включить список треков (например, «Любимые треки»)."""
        self._start({"uris": uris}, name, uris[0], check)

    def set_shuffle(self, on):
        dev = self._this_pc(self._wait_devices(start_app=False))
        q = f"?state={'true' if on else 'false'}" + (f"&device_id={dev['id']}" if dev else "")
        r = self._api("PUT", "/me/player/shuffle" + q)
        if r.status_code not in (200, 202, 204):
            raise SpotifyError(f"Spotify не смог переключить перемешивание ({r.status_code}: {_why(r)}).")

    def _start(self, body, name, open_uri, check, shuffle=None):
        """Включает в Spotify на этом компьютере и проверяет, что заиграло.

        check() -> (играет ли, название) — что реально играет, по данным Windows. Spotify для приложений
        в режиме разработки не сообщает, что сейчас играет, и иногда, приняв команду, ставит на паузу,
        поэтому шаги идут по очереди, пока Windows не покажет нужное:
          1) команда «играй» на этот ПК;
          2) открыть трек/плейлист в самом приложении (это «будит» плеер) + снова «играй»;
          3) нужная песня загружена, но на паузе — нажать мультимедийную клавишу Play.
        name — какая песня должна заиграть ("" — любая новая, например начало плейлиста)."""
        devices = self._wait_devices(start_app=True)
        print(f"[spotify] устройства: {[(d.get('name'), d.get('type'), d.get('is_active')) for d in devices]}")
        dev = self._this_pc(devices)
        if dev is None:
            raise SpotifyError("Не вижу Spotify на этом компьютере. Открой приложение Spotify и повтори.")
        if not dev.get("is_active"):
            self._api("PUT", "/me/player", json={"device_ids": [dev["id"]], "play": False})
            time.sleep(0.6)
        if shuffle is not None:
            self._api("PUT", f"/me/player/shuffle?state={'true' if shuffle else 'false'}&device_id={dev['id']}")

        want = _norm(name)
        before = ("", False)
        if check:
            try:
                before = check()[::-1]          # (название, играет ли)
            except Exception:  # noqa: BLE001
                pass

        def matches(on, title):
            if not on:
                return False
            if want:
                t = _norm(title)
                return bool(t) and (t == want or want in t or t in want)
            # нужна «любая новая»: играет и это не то, что играло до команды
            return not before[1] or _norm(title) != _norm(before[0])

        def playing_now(wait):
            """Ждёт до wait секунд, пока заиграет нужное. (заиграло?, что сейчас, играет ли)"""
            title, on = "", False
            for _ in range(int(wait / 0.5)):
                time.sleep(0.5)
                if check:
                    try:
                        on, title = check()
                    except Exception as e:  # noqa: BLE001
                        print(f"[spotify] проверка через Windows не удалась: {e!r} — считаю, что включилось")
                        return True, "", True
                else:
                    st = self._now_playing()
                    on, title = bool(st.get("is_playing")), (st.get("item") or {}).get("name", "")
                if matches(on, title):
                    return True, title, on
            return False, title, on

        def send_play():
            r = self._api("PUT", f"/me/player/play?device_id={dev['id']}", json=body)
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
                r = self._api("PUT", f"/me/player/play?device_id={dev['id']}", json=body)
                if r.status_code in (200, 202, 204):
                    return
            raise SpotifyError(f"Spotify не смог включить ({r.status_code}: {_why(r)}).")

        # 1) просто «играй»
        send_play()
        ok, title, on = playing_now(3.0)
        print(f"[spotify] шаг 1: {'играет' if ok else 'нет'} (сейчас: {title!r}, играет={on})")
        if ok:
            return
        # 2) открыть в приложении и снова «играй»
        try:
            os.startfile(open_uri)         # type: ignore[attr-defined]
            time.sleep(1.2)
        except (OSError, AttributeError):
            pass
        send_play()
        ok, title, on = playing_now(3.5)
        print(f"[spotify] шаг 2: {'играет' if ok else 'нет'} (сейчас: {title!r}, играет={on})")
        if ok:
            return
        # 3) нужная песня загружена, но стоит на паузе — нажимаем Play
        # только если Windows точно видит нужное на паузе — иначе клавиша выключила бы музыку
        loaded = bool(title) and ((want and (_norm(title) == want or want in _norm(title)))
                                  or (not want and _norm(title) != _norm(before[0])))
        if check and not on and loaded:
            from music import press
            press("play_pause")
            ok, title, on = playing_now(2.5)
            print(f"[spotify] шаг 3: {'играет' if ok else 'нет'} (сейчас: {title!r}, играет={on})")
            if ok:
                return
        if on and title and want:
            raise SpotifyError(f"Spotify вместо этого играет «{title}». Попробуй сказать название точнее.")
        raise SpotifyError("Spotify принял команду, но не заиграло. Нажми Play в Spotify один раз и повтори.")

    # ---------- плейлисты и любимые ----------

    def _need_library(self):
        granted = set((self.data.get("scope") or "").split())
        if not LIBRARY_SCOPES <= granted:
            raise NeedReauth("Чтобы видеть твои плейлисты, нужно ещё одно разрешение в Spotify.")

    def _get_json(self, url, params=None):
        r = self._api("GET", url, params=params)
        if r.status_code in (401, 403) and "scope" in _why(r).lower():
            raise NeedReauth("Чтобы видеть твои плейлисты, нужно ещё одно разрешение в Spotify.")
        if r.status_code != 200:
            print(f"[spotify] GET {url} {r.status_code}: {_why(r)!r}")
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def playlists(self):
        """Твои плейлисты (свои и на которые подписан): [{name, uri, id, own}]."""
        self._need_library()
        me = (self._get_json("/me") or {}).get("id", "")
        out = []
        for offset in range(0, 300, 50):
            page = self._get_json("/me/playlists", {"limit": 50, "offset": offset})
            if not page:
                break
            for p in page.get("items") or []:
                if p and p.get("uri"):
                    out.append({"name": p.get("name", ""), "uri": p["uri"], "id": p.get("id", ""),
                                "own": (p.get("owner") or {}).get("id") == me or p.get("collaborative", False)})
            if not page.get("next"):
                break
        return out

    def find_playlist(self, query):
        from apps import translit
        lists = self.playlists()
        q, lat = query.lower(), translit(query.lower())
        best, best_s = None, 0
        for p in lists:
            n = p["name"].lower()
            sc = max(fuzz.token_set_ratio(q, n), fuzz.ratio(q, n), fuzz.token_set_ratio(lat, translit(n)))
            if sc > best_s:
                best, best_s = p, sc
        print(f"[spotify] плейлист «{query}» -> {best and best['name']} ({best_s})")
        return best if best_s >= 70 else None

    def playlist_tracks(self, pid):
        """Треки плейлиста: [{uri, name, artists}]. Spotify отдаёт их только для твоих плейлистов."""
        out = []
        for offset in range(0, 500, 50):
            page = (self._get_json(f"/playlists/{pid}/items", {"limit": 50, "offset": offset})
                    or self._get_json(f"/playlists/{pid}/tracks", {"limit": 50, "offset": offset}))
            if not page:
                break
            for it in page.get("items") or []:
                t = (it or {}).get("item") or (it or {}).get("track") or {}
                if t.get("uri", "").startswith("spotify:track:"):
                    out.append({"uri": t["uri"], "name": t.get("name", ""),
                                "artists": ", ".join(a.get("name", "") for a in t.get("artists") or [])})
            if not page.get("next"):
                break
        return out

    def find_in_playlist(self, tracks, query):
        from apps import translit
        q, lat = query.lower(), translit(query.lower())
        best, best_s = None, 0
        for t in tracks:
            full = f"{t['name']} {t['artists']}".lower()
            sc = max(fuzz.token_set_ratio(q, full), fuzz.partial_ratio(q, t["name"].lower()),
                     fuzz.token_set_ratio(lat, translit(full)))
            if sc > best_s:
                best, best_s = t, sc
        return best if best_s >= 70 else None

    def liked_tracks(self, limit=100):
        self._need_library()
        out = []
        for offset in range(0, limit, 50):
            page = self._get_json("/me/tracks", {"limit": 50, "offset": offset})
            if not page:
                break
            out += [(it.get("track") or it.get("item") or {}).get("uri") for it in page.get("items") or []]
            if not page.get("next"):
                break
        return [u for u in out if u]


def _norm(text):
    text = (text or "").lower()
    text = re.sub(r"\s*[\(\[].*?[\)\]]", "", text)       # «Numb (Live)» -> «numb»
    text = re.sub(r"\s+-\s+.*$", "", text)                # «In the End - 2020 Remaster» -> «in the end»
    return re.sub(r"[^\w ]+", "", text).strip()


# ---------- разбор фразы ----------

VERB = r"(?:включи|поставь|найди|запусти|сыграй|играй|врубай|вруби|давай)"
PLAY_RE = re.compile(VERB + r"\s+(?:мне\s+)?(?:песню|трек|композицию|песенку)\s+(?P<q>.+)$")
SPOTIFY_RE = re.compile(VERB + r"\s+(?:мне\s+)?(?P<q>.+?)\s+(?:в|на|из)\s+спот[иы]ф\w*$")
PLAYLIST_RE = re.compile(VERB + r"\s+(?:мне\s+)?(?:мой\s+|моих\s+)?(?:плейлист|плей лист|подборку)\s+(?P<pl>.+)$")
FROM_PL_RE = re.compile(r"^(?P<q>.+?)\s+(?:из|с)\s+(?:моего\s+)?(?:плейлиста|плей листа|подборки)\s+(?P<pl>.+)$")
LIKED_RE = re.compile(VERB + r"\s+(?:мне\s+)?(?:мои\s+|мою\s+)?(?:любим\w*|лайкнут\w*|понравивш\w*|сохран[её]нн\w*|избранн\w*)"
                      r"(?:\s+(?:треки|песни|музыку))?\s*$")
SHUFFLE_WORDS = re.compile(r"\s*\b(?:вперемешку|в случайном порядке|рандомно|в разнобой|шафл\w*|перемешав|перемешанн\w*)\b")
NOT_QUERY = {"на паузу", "дальше", "следующую", "другую", "предыдущую", "заново", "погромче", "потише"}


def _clean_q(q):
    q = q.strip(" .,!?«»\"")
    q = re.sub(r"^(?:под названием|называется)\s+", "", q)
    q = re.sub(r"\s+(?:от|исполнителя|группы|исполнитель|в исполнении)\s+", " ", q)
    q = re.sub(r"\s+(?:в|на|из)\s+спот[иы]ф\w*$", "", q)
    return q.strip(" .,!?«»\"")


def parse_request(text):
    """Что просят включить. Возвращает dict или None (это не просьба к Spotify):
       {"kind": "track", "q": ...}
       {"kind": "track_in_playlist", "q": ..., "playlist": ...}
       {"kind": "playlist", "playlist": ..., "shuffle": bool}
       {"kind": "liked", "shuffle": bool}
       {"kind": "shuffle", "on": bool}"""
    t = text.lower().strip(" .!?")
    if re.search(r"\b(?:перемешай|включи перемешивание|включи случайный порядок|включи шафл)\b", t):
        return {"kind": "shuffle", "on": True}
    if re.search(r"\b(?:выключи|отключи|не)\s+(?:перемешивание|перемешивай|случайный порядок|шафл)\b", t):
        return {"kind": "shuffle", "on": False}
    shuffle = bool(SHUFFLE_WORDS.search(t))
    t = SHUFFLE_WORDS.sub("", t).strip()
    if LIKED_RE.search(t) and "плейлист" not in t:
        return {"kind": "liked", "shuffle": shuffle}
    m = PLAYLIST_RE.search(t)
    if m:
        pl = _clean_q(m.group("pl"))
        return {"kind": "playlist", "playlist": pl, "shuffle": shuffle} if len(pl) >= 2 else None
    m = PLAY_RE.search(t) or SPOTIFY_RE.search(t)
    if not m:
        return None
    q = _clean_q(m.group("q"))
    f = FROM_PL_RE.search(q)
    if f:
        return {"kind": "track_in_playlist", "q": _clean_q(f.group("q")), "playlist": _clean_q(f.group("pl"))}
    if len(q) < 2 or q in NOT_QUERY:
        return None
    return {"kind": "track", "q": q}
