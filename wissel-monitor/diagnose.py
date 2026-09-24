"""Tijdelijke diagnose: welke wissel.nl-endpoints en user-agents werken vanaf deze machine?"""
import time
import urllib.error
import urllib.request

URLS = [
    "https://www.wissel.nl/products.json?limit=250",
    "https://www.wissel.nl/products.json?limit=30",
    "https://www.wissel.nl/collections/coolblue-korting/products.json",
    "https://www.wissel.nl/collections/all.atom",
    "https://www.wissel.nl/collections/coolblue-korting",
    "https://www.wissel.nl/kopen/cadeaubonnen-met-korting/coolblue",
    "https://www.wissel.nl/sitemap.xml",
    "https://www.wissel.nl/",
]
UAS = {
    "iphone": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "python": "Python-urllib/3.12",
    "bot": "wissel-monitor/1.0 (+https://github.com/samvandewiel/furniture-app)",
}
for name, ua in UAS.items():
    for url in URLS:
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read()
                print(f"{name:7} {r.status} {len(body):>8}B {r.headers.get('Content-Type','')[:30]:30} {url}")
                if url.endswith("coolblue") and name == "iphone":
                    print("   snippet:", body[:1500].decode("utf-8", "replace").replace("\n", " ")[:1500])
        except urllib.error.HTTPError as e:
            print(f"{name:7} {e.code} {'':>9} {e.headers.get('Server','')[:15]:15} cf-ray={e.headers.get('cf-ray','-')} {url}")
        except Exception as e:
            print(f"{name:7} ERR {e} {url}")
        time.sleep(3)
