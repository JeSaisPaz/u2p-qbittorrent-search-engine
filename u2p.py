import urllib.request
import urllib.parse
import urllib.error
import json
import re
import sys

# ─────────────────────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────────────────────

IPNS_KEY = "k51qzi5uqu5dm8jpvts4lrquwavjc64oiv3ok7ztc7ojgtzmiot49qbixk3var"
SITE_URL = "https://ipfs.io/ipns/" + IPNS_KEY

# Passerelles IPFS — essayées dans l'ordre
IPFS_GATEWAYS = [
    "https://ipfs.io",
    "https://cloudflare-ipfs.com",
    "https://dweb.link",
    "https://gateway.pinata.cloud",
    "https://4everland.io",
]

# API REST de secours (torrent-paradise.ml — même dataset)
FALLBACK_API = "https://torrent-paradise.ml/api/search?q={q}"

TIMEOUT = 15  # secondes (court pour ne pas bloquer qBittorrent)

TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
]
TRACKER_PARAMS = "&".join("tr=" + urllib.parse.quote(t, safe="") for t in TRACKERS)

# ─────────────────────────────────────────────────────────────────────────────
#  Porter Stemmer (identique à celui embarqué dans le bundle JS du site)
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

    # helpers
    def _v(self, w, i):          # is vowel?
        if w[i] in "aeiou": return True
        if w[i] != "y":     return False
        return i > 0 and not self._v(w, i - 1)

    def _m(self, w, end=None):   # count VC sequences
        if end is None: end = len(w)
        n, vowel = 0, False
        for i in range(end):
            if self._v(w, i):  vowel = True
            elif vowel:        n += 1; vowel = False
        return n

    def _has_v(self, w, j):
        return any(self._v(w, i) for i in range(j))

    def _dbl(self, w):
        return len(w) >= 2 and w[-1] == w[-2] and not self._v(w, len(w)-1)

    def _cvc(self, w):
        if len(w) < 3: return False
        i = len(w) - 1
        return (not self._v(w, i) and self._v(w, i-1) and
                not self._v(w, i-2) and w[i] not in "wxy")

    def _end(self, w, s):        # test suffix, set self._j
        if w.endswith(s):
            self._j = len(w) - len(s)
            return True
        return False

    def _set(self, w, s):        return w[:self._j] + s
    def _R(self, w, s):          return self._set(w, s) if self._m(w, self._j) > 0 else w

    def _1a(self, w):
        if   self._end(w, "sses"): return self._set(w, "ss")
        elif self._end(w, "ies"):  return self._set(w, "i")
        elif self._end(w, "ss"):   return w
        elif self._end(w, "s"):    return w[:self._j]
        return w

    def _1b(self, w):
        if self._end(w, "eed"):
            return w[:-1] if self._m(w, self._j) > 0 else w
        for suf in ("ed", "ing"):
            if self._end(w, suf) and self._has_v(w, self._j):
                w = w[:self._j]
                if   self._end(w, "at"): return self._set(w, "ate")
                elif self._end(w, "bl"): return self._set(w, "ble")
                elif self._end(w, "iz"): return self._set(w, "ize")
                elif self._dbl(w) and w[-1] not in "lsz": return w[:-1]
                elif self._m(w) == 1 and self._cvc(w):    return w + "e"
                return w
        return w

    def _1c(self, w):
        if self._end(w, "y") and self._has_v(w, self._j):
            return self._set(w, "i")
        return w

    def _2(self, w):
        for a, b in [("ational","ate"),("tional","tion"),("enci","ence"),("anci","ance"),
                     ("izer","ize"),("bli","ble"),("alli","al"),("entli","ent"),("eli","e"),
                     ("ousli","ous"),("ization","ize"),("ation","ate"),("ator","ate"),
                     ("alism","al"),("iveness","ive"),("fulness","ful"),("ousness","ous"),
                     ("aliti","al"),("iviti","ive"),("biliti","ble"),("logi","log")]:
            if self._end(w, a): return self._R(w, b)
        return w

    def _3(self, w):
        for a, b in [("icate","ic"),("ative",""),("alize","al"),
                     ("iciti","ic"),("ical","ic"),("ful",""),("ness","")]:
            if self._end(w, a): return self._R(w, b)
        return w

    def _4(self, w):
        for s in ["al","ance","ence","er","ic","able","ible","ant","ement","ment","ent",
                  "ou","ism","ate","iti","ous","ive","ize"]:
            if self._end(w, s) and self._m(w, self._j) > 1:
                return w[:self._j]
        if self._end(w, "ion") and self._m(w, self._j) > 1 and w[self._j-1:self._j] in ("s","t"):
            return w[:self._j]
        return w

    def _5a(self, w):
        if self._end(w, "e"):
            m = self._m(w, self._j)
            if m > 1 or (m == 1 and not self._cvc(w[:self._j])):
                return w[:self._j]
        return w

    def _5b(self, w):
        if w.endswith("ll") and self._m(w, len(w)-1) > 1:
            return w[:-1]
        return w


_stemmer = _Porter()


def _stem_query(query):
    """Tokenise et stemme la requête. Retourne une liste dédupliquée."""
    tokens = re.findall(r"[a-zA-Z]+", query.lower())
    return list(dict.fromkeys(_stemmer.stem(t) for t in tokens if len(t) > 1))


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
}


def _get(url, timeout=TIMEOUT):
    """GET → str | None"""
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[U2P] GET failed {url!r}: {e}", file=sys.stderr)
        return None


def _get_json(url, timeout=TIMEOUT):
    """GET → parsed JSON | None"""
    text = _get(url, timeout)
    if not text:
        return None
    # Strip BOM / whitespace
    text = text.lstrip("\ufeff").strip()
    try:
        return json.loads(text)
    except Exception as e:
        print(f"[U2P] JSON parse error from {url!r}: {e}", file=sys.stderr)
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Stratégie 1 — Index shardé IPFS (une requête par mot stemmé)
# ─────────────────────────────────────────────────────────────────────────────

def _word_url(gateway, word):
    """URL du fichier JSON pour un mot stemmé dans l'index shardé."""
    return f"{gateway}/ipns/{IPNS_KEY}/index/{word}.json"


def _search_ipfs(words):
    """
    Cherche dans l'index shardé IPFS.
    Pour chaque passerelle, tente de récupérer <gateway>/ipns/<IPNS>/index/<mot>.json
    Retourne une liste de dicts ou [] en cas d'échec.
    """
    for gw in IPFS_GATEWAYS:
        result_sets = []
        ok = True
        for word in words:
            url = _word_url(gw, word)
            data = _get_json(url)
            if data is None:
                ok = False
                break
            # Normalise : liste de dicts ou liste de listes
            docs = _normalize_docs(data)
            if not docs:
                return []  # mot présent mais aucun résultat
            result_sets.append({d["id"]: d for d in docs if d.get("id")})

        if ok and result_sets:
            # Intersection
            common = set(result_sets[0])
            for rs in result_sets[1:]:
                common &= set(rs)
            return [result_sets[0][k] for k in common]

    return []


def _normalize_docs(data):
    """
    L'index peut stocker les docs comme :
      - liste de dicts   : [{"id":..,"text":..,"len":..,"s":..,"l":..}, ...]
      - liste de listes  : [[infohash, name, size, seeds, leechers], ...]
    Retourne toujours une liste de dicts.
    """
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append({
                "id":   item[0],
                "text": item[1],
                "len":  item[2] if len(item) > 2 else 0,
                "s":    item[3] if len(item) > 3 else 0,
                "l":    item[4] if len(item) > 4 else 0,
            })
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Stratégie 2 — API REST torrent-paradise.ml (fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _search_api(query):
    """
    Appelle l'API REST de torrent-paradise.ml.
    Réponse : [{"id":..,"text":..,"len":..,"s":..,"l":..}, ...]
    """
    url = FALLBACK_API.format(q=urllib.parse.quote(query, safe=""))
    data = _get_json(url)
    if isinstance(data, list):
        return _normalize_docs(data)
    return []


# ─────────────────────────────────────────────────────────────────────────────
#  Classe plugin qBittorrent
# ─────────────────────────────────────────────────────────────────────────────

class u2p:
    """Plugin de recherche qBittorrent pour U2P / Torrent Paradise (IPFS)."""

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
        words = _stem_query(what)
        print(f"[U2P] Mots stemmés : {words}", file=sys.stderr)

        # Tentative 1 : index shardé IPFS
        results = _search_ipfs(words) if words else []

        # Tentative 2 : API REST (fallback)
        if not results:
            print("[U2P] IPFS échoué, tentative API REST...", file=sys.stderr)
            results = _search_api(what)

        print(f"[U2P] {len(results)} résultat(s) trouvé(s).", file=sys.stderr)

        for item in results:
            try:
                infohash = str(item.get("id",   "") or "").strip()
                name     = str(item.get("text", "") or "").strip()
                size     = int(item.get("len",  0) or 0)
                seeds    = int(item.get("s",    0) or 0)
                leechers = int(item.get("l",    0) or 0)

                if not infohash or not name:
                    continue

                magnet = (
                    "magnet:?xt=urn:btih:{ih}&dn={dn}&{tr}".format(
                        ih=infohash,
                        dn=urllib.parse.quote(name, safe=""),
                        tr=TRACKER_PARAMS,
                    )
                )

                desc = f"{SITE_URL}#/detail/{infohash}"

                # Format nova3 : link|name|size|seeds|leechers|engine_url|desc_link
                print(f"{magnet}|{name}|{size}|{seeds}|{leechers}|{SITE_URL}|{desc}")

            except Exception as e:
                print(f"[U2P] Erreur résultat : {e}", file=sys.stderr)
