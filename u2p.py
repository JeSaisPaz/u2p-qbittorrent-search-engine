import urllib.request
import urllib.parse
import urllib.error
import json
import sys

IPNS_KEY = "k51qzi5uqu5dm8jpvts4lrquwavjc64oiv3ok7ztc7ojgtzmiot49qbixk3var"

GATEWAYS = [
    # API REST officielle (la plus rapide)
    "https://torrent-paradise.ml/api/search?q={query}",
    # Passerelles IPFS de secours — le site IPFS lui-même est statique et ne
    # possède pas d'API de recherche côté serveur ; on retombe donc sur l'API
    # REST via les URLs de substitution connues.
    "https://cloudflare-ipfs.com/ipns/{ipns}/api/search?q={query}",
    "https://ipfs.io/ipns/{ipns}/api/search?q={query}",
]

# Trackers publics ajoutés aux liens magnet pour améliorer la disponibilité
TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
    "https://tracker.gbitt.info/announce",
]

TRACKER_PARAMS = "&".join(
    "tr=" + urllib.parse.quote(t, safe="") for t in TRACKERS
)

ENGINE_URL = "https://torrent-paradise.ml"
TIMEOUT = 20  # secondes


class u2p:
    """Plugin de recherche qBittorrent pour U2P / Torrent Paradise."""

    name = "U2P - Torrent Paradise (IPFS)"
    url = ENGINE_URL
    public = True
    supported_categories = {
        "all": "all",
        "movies": "all",
        "tv": "all",
        "music": "all",
        "games": "all",
        "anime": "all",
        "software": "all",
        "books": "all",
        "pictures": "all",
    }

    def search(self, what, cat="all"):
        """Lance la recherche et affiche les résultats sur stdout."""
        query = urllib.parse.quote(what, safe="")
        results = self._fetch_results(query)

        for item in results:
            try:
                infohash = item.get("id", "").strip()
                name = item.get("text", "").strip()
                size = int(item.get("len", 0))
                seeds = int(item.get("s", 0))
                leechers = int(item.get("l", 0))

                if not infohash or not name:
                    continue

                magnet = (
                    "magnet:?xt=urn:btih:{ih}&dn={dn}&{tr}".format(
                        ih=infohash,
                        dn=urllib.parse.quote(name, safe=""),
                        tr=TRACKER_PARAMS,
                    )
                )

                desc_link = "{base}/detail/{ih}".format(
                    base=ENGINE_URL, ih=infohash
                )

                # Format qBittorrent : link|name|size|seeds|leechers|engine_url|desc_link
                print(
                    "{link}|{name}|{size}|{seeds}|{leechers}|{engine}|{desc}".format(
                        link=magnet,
                        name=name,
                        size=size,
                        seeds=seeds,
                        leechers=leechers,
                        engine=ENGINE_URL,
                        desc=desc_link,
                    )
                )
            except Exception:
                # Ne jamais afficher d'erreurs sur stdout (réservé aux résultats)
                pass

    def _fetch_results(self, encoded_query):
        for gateway_tpl in GATEWAYS:
            url = gateway_tpl.format(query=encoded_query, ipns=IPNS_KEY)
            try:
                data = self._http_get(url)
                if data:
                    parsed = json.loads(data)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        return parsed
            except Exception:
                pass
        return []

    def _http_get(self, url):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; qBittorrent search plugin; "
                "U2P/1.1; +https://torrent-paradise.ml)"
            ),
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                charset = "utf-8"
                ct = resp.headers.get_content_charset()
                if ct:
                    charset = ct
                return raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as e:
            print(
                "U2P HTTP error {}: {}".format(e.code, url),
                file=sys.stderr,
            )
        except urllib.error.URLError as e:
            print(
                "U2P URL error {}: {}".format(e.reason, url),
                file=sys.stderr,
            )
        return None