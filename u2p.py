import json
import queue
import re
import sys
import threading
import urllib.parse
import urllib.request

# ─────────────────────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────────────────────

IPNS_KEY = "k51qzi5uqu5dm8jpvts4lrquwavjc64oiv3ok7ztc7ojgtzmiot49qbixk3var"
SITE_URL = "https://ipfs.io/ipns/" + IPNS_KEY

REST_APIS = [
    "https://torrent-paradise.ml/api/search?q={q}",
    "https://tp.p2p.im/api/search?q={q}",
]

IPFS_GATEWAYS = [
    "https://ipfs.io",
    "https://cloudflare-ipfs.com",
    "https://dweb.link",
    "https://gateway.pinata.cloud",
    "https://4everland.io",
    "https://hardbin.com",
]

PER_REQUEST_TIMEOUT = 8   # secondes par requête HTTP
GLOBAL_TIMEOUT      = 20  # secondes : limite absolue pour la recherche entière

TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
]
TR = "&".join("tr=" + urllib.parse.quote(t, safe="") for t in TRACKERS)

# ─────────────────────────────────────────────────────────────────────────────
#  Porter Stemmer (identique au bundle JS du site)
# ─────────────────────────────────────────────────────────────────────────────

class _Porter:
    def stem(self, w):
        w = w.lower()
        if len(w) <= 2:
            return w
        for fn in (self._1a, self._1b, self._1c,
                   self._2,  self._3,  self._4,
                   self._5a, self._5b):
            w = fn(w)
        return w

    def _v(self, w, i):
        c = w[i]
        if c in "aeiou": return True
        if c != "y":     return False
        return i > 0 and not self._v(w, i - 1)

    def _m(self, w, end=None):
        if end is None: end = len(w)
        n, vow = 0, False
        for i in range(end):
            if self._v(w, i):  vow = True
            elif vow:          n += 1; vow = False
        return n

    def _hv(self, w, j): return any(self._v(w, i) for i in range(j))

    def _dbl(self, w):
        return len(w) >= 2 and w[-1] == w[-2] and not self._v(w, len(w)-1)

    def _cvc(self, w):
        if len(w) < 3: return False
        i = len(w) - 1
        return (not self._v(w, i) and self._v(w, i-1)
                and not self._v(w, i-2) and w[i] not in "wxy")

    def _e(self, w, s):
        if w.endswith(s): self._j = len(w)-len(s); return True
        return False

    def _s(self, w, r): return w[:self._j] + r
    def _R(self, w, r): return self._s(w, r) if self._m(w, self._j) > 0 else w

    def _1a(self, w):
        if   self._e(w,"sses"): return self._s(w,"ss")
        elif self._e(w,"ies"):  return self._s(w,"i")
        elif self._e(w,"ss"):   return w
        elif self._e(w,"s"):    return w[:self._j]
        return w

    def _1b(self, w):
        if self._e(w,"eed"):
            return w[:-1] if self._m(w,self._j)>0 else w
        for suf in ("ed","ing"):
            if self._e(w,suf) and self._hv(w,self._j):
                w = w[:self._j]
                if   self._e(w,"at"): return self._s(w,"ate")
                elif self._e(w,"bl"): return self._s(w,"ble")
                elif self._e(w,"iz"): return self._s(w,"ize")
                elif self._dbl(w) and w[-1] not in "lsz": return w[:-1]
                elif self._m(w)==1 and self._cvc(w):      return w+"e"
                return w
        return w

    def _1c(self, w):
        if self._e(w,"y") and self._hv(w,self._j): return self._s(w,"i")
        return w

    def _2(self, w):
        for a,b in [("ational","ate"),("tional","tion"),("enci","ence"),("anci","ance"),
                    ("izer","ize"),("bli","ble"),("alli","al"),("entli","ent"),("eli","e"),
                    ("ousli","ous"),("ization","ize"),("ation","ate"),("ator","ate"),
                    ("alism","al"),("iveness","ive"),("fulness","ful"),("ousness","ous"),
                    ("aliti","al"),("iviti","ive"),("biliti","ble"),("logi","log")]:
            if self._e(w,a): return self._R(w,b)
        return w

    def _3(self, w):
        for a,b in [("icate","ic"),("ative",""),("alize","al"),
                    ("iciti","ic"),("ical","ic"),("ful",""),("ness","")]:
            if self._e(w,a): return self._R(w,b)
        return w

    def _4(self, w):
        for s in ["al","ance","ence","er","ic","able","ible","ant","ement","ment",
                  "ent","ou","ism","ate","iti","ous","ive","ize"]:
            if self._e(w,s) and self._m(w,self._j)>1: return w[:self._j]
        if (self._e(w,"ion") and self._m(w,self._j)>1
                and self._j>0 and w[self._j-1] in "st"):
            return w[:self._j]
        return w

    def _5a(self, w):
        if self._e(w,"e"):
            m = self._m(w,self._j)
            if m>1 or (m==1 and not self._cvc(w[:self._j])): return w[:self._j]
        return w

    def _5b(self, w):
        if w.endswith("ll") and self._m(w,len(w)-1)>1: return w[:-1]
        return w


_stemmer = _Porter()


def _stem_query(query):
    tokens = re.findall(r"[a-zA-Z]+", query.lower())
    seen, out = set(), []
    for t in tokens:
        if len(t) > 1:
            s = _stemmer.stem(t)
            if s not in seen:
                seen.add(s); out.append(s)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP
# ─────────────────────────────────────────────────────────────────────────────

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")


def _fetch_json(url):
    """GET → liste | None. Ne lève jamais d'exception."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=PER_REQUEST_TIMEOUT) as r:
            raw = r.read().decode("utf-8", errors="replace").lstrip("\ufeff").strip()
            data = json.loads(raw)
            if isinstance(data, list):
                return data
    except Exception as exc:
        print(f"[U2P] {url!r}: {exc}", file=sys.stderr)
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Normalisation
# ─────────────────────────────────────────────────────────────────────────────

def _norm(items):
    out = []
    for it in items:
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            out.append({"id": it[0], "text": it[1],
                        "len": it[2] if len(it)>2 else 0,
                        "s":   it[3] if len(it)>3 else 0,
                        "l":   it[4] if len(it)>4 else 0})
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Workers (threads daemon)
# ─────────────────────────────────────────────────────────────────────────────

def _worker_rest(tpl, query, q):
    url  = tpl.format(q=urllib.parse.quote(query, safe=""))
    data = _fetch_json(url)
    if data:
        q.put(_norm(data))


def _worker_ipfs(gateway, words, q):
    sets = []
    for word in words:
        url  = f"{gateway}/ipns/{IPNS_KEY}/index/{word}.json"
        data = _fetch_json(url)
        if data is None:
            return                      # passerelle KO pour ce mot
        docs = _norm(data)
        if not docs:
            q.put([])                   # mot trouvé, aucun résultat
            return
        sets.append({d["id"]: d for d in docs if d.get("id")})
    if not sets:
        return
    common = set(sets[0])
    for s in sets[1:]:
        common &= set(s)
    q.put([sets[0][k] for k in common])


# ─────────────────────────────────────────────────────────────────────────────
#  Orchestrateur parallèle
# ─────────────────────────────────────────────────────────────────────────────

def _search(query):
    words = _stem_query(query)
    rq    = queue.Queue()

    threads = []
    for tpl in REST_APIS:
        t = threading.Thread(target=_worker_rest, args=(tpl, query, rq), daemon=True)
        threads.append(t)
    if words:
        for gw in IPFS_GATEWAYS:
            t = threading.Thread(target=_worker_ipfs, args=(gw, words, rq), daemon=True)
            threads.append(t)

    total = len(threads)
    for t in threads:
        t.start()

    # Retourne le premier résultat non-vide, ou [] au bout de GLOBAL_TIMEOUT s
    answered = 0
    import time
    deadline = time.time() + GLOBAL_TIMEOUT
    while time.time() < deadline:
        try:
            result = rq.get(timeout=0.5)
            answered += 1
            if result:
                return result
            if answered >= total:
                break           # tous les workers ont répondu, tous vides
        except queue.Empty:
            pass

    return []


# ─────────────────────────────────────────────────────────────────────────────
#  Classe plugin qBittorrent (interface nova3)
# ─────────────────────────────────────────────────────────────────────────────

class u2p:
    """Plugin qBittorrent pour U2P / Torrent Paradise (IPFS)."""

    name    = "U2P - Torrent Paradise (IPFS)"
    url     = SITE_URL
    public  = True
    supported_categories = {
        "all":      "all",
        "movies":   "all",
        "tv":       "all",
        "music":    "all",
        "games":    "all",
        "anime":    "all",
        "software": "all",
        "books":    "all",
        "pictures": "all",
    }

    def search(self, what, cat="all"):
        results = _search(what)

        for item in results:
            try:
                ih   = str(item.get("id",   "") or "").strip()
                name = str(item.get("text", "") or "").strip()
                size = int(item.get("len",  0)  or 0)
                se   = int(item.get("s",    0)  or 0)
                le   = int(item.get("l",    0)  or 0)

                if not ih or not name:
                    continue

                magnet = (f"magnet:?xt=urn:btih:{ih}"
                          f"&dn={urllib.parse.quote(name, safe='')}"
                          f"&{TR}")
                desc = f"{SITE_URL}#/detail/{ih}"

                # Format nova3 : link|name|size|seeds|leechers|engine_url|desc_link
                print(f"{magnet}|{name}|{size}|{se}|{le}|{SITE_URL}|{desc}")

            except Exception as exc:
                print(f"[U2P] result error: {exc}", file=sys.stderr)
