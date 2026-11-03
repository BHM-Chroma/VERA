"""Поиск в интернете: «загугли …».

Google открывается в браузере, как будто запрос набрали сами (без ключей и API).
Параллельно VERONIKA сама читает список результатов (DuckDuckGo, запасной — Bing; Google таких
«невидимых читателей» блокирует капчей) и показывает их в окошке с номерами: «открой вторую».
"""
import html
import re
import urllib.parse
from html.parser import HTMLParser

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")

SEARCH_RE = re.compile(
    r"^(?:(?:загугли|погугли|нагугли|гугли|загуглить)\s+(?:мне\s+)?(?:про\s+)?"
    r"|(?:найди|поищи|ищи|посмотри)\s+(?:мне\s+)?(?:в\s+(?:интернете|инете|инторнете|сети|гугле|google|гугл)"
    r"|через\s+(?:гугл|google))\s+(?:про\s+)?)(?P<q>.+)$")
# «загугли» в конце: «котики загугли»
SEARCH_END_RE = re.compile(r"^(?P<q>.+?)\s+(?:загугли|погугли)$")

ORDINAL_WORDS = [   # (шаблон всего слова, индекс)
    (r"перв(?:ая|ую|ой|ый|ое|ого)|1|один|одну", 0), (r"втор(?:ая|ую|ой|ое|ого)|2|два|две", 1),
    (r"трет(?:ья|ью|ий|ье|ьего|ей)|3|три", 2), (r"четв[её]рт(?:ая|ую|ый|ое|ой|ого)|4|четыре", 3),
    (r"пят(?:ая|ую|ый|ое|ой|ого)|5|пять", 4),
]
PICK_RE = re.compile(r"^(?:открой|выбери|давай|включи|покажи|перейди\s+(?:на|по))?\s*(?:мне\s+)?"
                     r"(?:номер\s+)?(?P<n>\S+)(?:\s+(?:ссылку|ссылка|результат|сайт|вариант|номер))?$")


def parse_request(text):
    """«загугли как почистить кулер» -> «как почистить кулер»; None — это не поиск в интернете."""
    t = text.strip(" .!?")
    low = t.lower()
    m = SEARCH_RE.match(low) or SEARCH_END_RE.match(low)
    if not m:
        return None
    q = t[m.start("q"):m.end("q")].strip(" .,!?«»\"")      # регистр как сказали/написали
    return q if len(q) >= 2 else None


def parse_pick(text):
    """«открой вторую ссылку» -> 1 (индекс); None — это не выбор из списка."""
    t = text.lower().strip(" .!?")
    m = PICK_RE.match(t)
    if not m:
        return None
    n = m.group("n")
    for pattern, idx in ORDINAL_WORDS:
        if re.fullmatch(pattern, n):
            return idx
    return None


def google_url(q):
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)


# ---------- чтение результатов ----------

class _DDGParser(HTMLParser):
    """Разбор html.duckduckgo.com: div.result > a.result__a (заголовок), a.result__url, a.result__snippet."""

    def __init__(self):
        super().__init__()
        self.items, self._cur, self._field, self._ad = [], None, None, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if tag == "div" and "result" in cls.split() and "results_links" in cls:
            self._cur = {"title": "", "url": "", "snippet": ""}
            self._ad = "result--ad" in cls
            self.items.append(self._cur)
        elif self._cur is not None and tag == "a":
            if "result__a" in cls:
                self._field = "title"
                self._cur["url"] = _ddg_target(a.get("href", ""))
            elif "result__snippet" in cls:
                self._field = "snippet"
        if self._ad and self._cur is not None:
            self._cur["ad"] = True

    def handle_endtag(self, tag):
        if tag == "a":
            self._field = None

    def handle_data(self, data):
        if self._cur is not None and self._field:
            self._cur[self._field] += data


def _ddg_target(href):
    """«//duckduckgo.com/l/?uddg=https%3A%2F%2F…&rut=…» -> настоящий адрес."""
    href = html.unescape(href or "")
    if href.startswith("//"):
        href = "https:" + href
    q = urllib.parse.urlparse(href)
    if "duckduckgo.com" in q.netloc and q.path.startswith("/l/"):
        target = urllib.parse.parse_qs(q.query).get("uddg", [""])[0]
        return target
    return href


def _bing_target(href):
    """«https://www.bing.com/ck/a?…&u=a1aHR0cHM6Ly9…» -> настоящий адрес."""
    href = html.unescape(href or "")
    q = urllib.parse.urlparse(href)
    if "bing.com" in q.netloc and q.path.startswith("/ck/"):
        u = urllib.parse.parse_qs(q.query).get("u", [""])[0]
        if u.startswith("a1"):
            import base64
            try:
                return base64.urlsafe_b64decode(u[2:] + "=" * (-len(u[2:]) % 4)).decode("utf-8", "replace")
            except ValueError:
                return ""
    return href


class _BingParser(HTMLParser):
    """Разбор bing.com: li.b_algo > h2 > a (заголовок и ссылка), p (описание)."""

    def __init__(self):
        super().__init__()
        self.items, self._cur, self._field, self._in_h2 = [], None, None, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if tag == "li" and "b_algo" in cls.split():
            self._cur = {"title": "", "url": "", "snippet": ""}
            self.items.append(self._cur)
        elif self._cur is not None:
            if tag == "h2":
                self._in_h2 = True
            elif tag == "a" and self._in_h2 and not self._cur["url"]:
                self._cur["url"] = _bing_target(a.get("href", ""))
                self._field = "title"
            elif tag == "p" and not self._cur["snippet"]:
                self._field = "snippet"

    def handle_endtag(self, tag):
        if tag in ("a", "p"):
            self._field = None
        if tag == "h2":
            self._in_h2 = False

    def handle_data(self, data):
        if self._cur is not None and self._field:
            self._cur[self._field] += data


def _clean(items, limit):
    out, seen = [], set()
    for it in items:
        url = (it.get("url") or "").strip()
        title = re.sub(r"\s+", " ", it.get("title", "")).strip()
        if it.get("ad") or not url.startswith("http") or not title or url in seen:
            continue
        seen.add(url)
        site = urllib.parse.urlparse(url).netloc.lower()
        site = site[4:] if site.startswith("www.") else site
        out.append({"title": title[:110], "url": url, "site": site,
                    "snippet": re.sub(r"\s+", " ", it.get("snippet", "")).strip()[:200]})
        if len(out) >= limit:
            break
    return out


def parse_ddg(page, limit=5):
    p = _DDGParser()
    p.feed(page)
    return _clean(p.items, limit)


def parse_bing(page, limit=5):
    p = _BingParser()
    p.feed(page)
    return _clean(p.items, limit)


def results(q, limit=5):
    """Список результатов [{title, url, site, snippet}] или [] (если ни один поисковик не ответил)."""
    headers = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}
    try:
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": q, "kl": "ru-ru"},
                          headers=headers, timeout=8)
        if r.status_code == 200:
            found = parse_ddg(r.text, limit)
            if found:
                return found
            print(f"[поиск] DuckDuckGo: пусто (капча?), {len(r.text)} байт")
        else:
            print(f"[поиск] DuckDuckGo ответил {r.status_code}")
    except requests.RequestException as e:
        print(f"[поиск] DuckDuckGo недоступен: {e!r}")
    try:
        r = requests.get("https://www.bing.com/search", params={"q": q, "setlang": "ru", "cc": "RU"},
                         headers=headers, timeout=8)
        if r.status_code == 200:
            found = parse_bing(r.text, limit)
            if found:
                return found
            print("[поиск] Bing: пусто")
        else:
            print(f"[поиск] Bing ответил {r.status_code}")
    except requests.RequestException as e:
        print(f"[поиск] Bing недоступен: {e!r}")
    return []
