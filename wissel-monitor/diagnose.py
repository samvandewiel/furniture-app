"""Tijdelijke diagnose: structuur van de wissel.nl-merkpagina's."""
import re
import time
import urllib.request

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.geturl(), r.read().decode("utf-8", "replace")


_, sm = get("https://www.wissel.nl/sitemap.xml")
locs = re.findall(r"<loc>([^<]+)</loc>", sm)
print("SITEMAP", len(locs))
for l in locs:
    if re.search(r"coolblue|apple|media|bol", l, re.I):
        print("  ", l)
time.sleep(2)

final, html = get("https://www.wissel.nl/kopen/cadeaubonnen-met-korting/coolblue")
print("FINAL", final)
body = re.sub(r"<(script|style|svg)\b.*?</\1>", "", html, flags=re.S)
idx = [m.start() for m in re.finditer("€", body)]
print("EURO COUNT", len(idx))
start = max(0, idx[0] - 3000) if idx else 0
chunk = re.sub(r"\s+", " ", body[start:start + 9000])
for i in range(0, len(chunk), 1500):
    print("HTML", chunk[i:i + 1500])
for m in re.finditer(r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.*?)</script>', html, re.S):
    print("JSON", m.group(1)[:1500].replace("\n", " "))
for m in re.finditer(r'data-[\w-]+-value="([^"]{0,300})"', html):
    if "&quot;" in m.group(1) or "price" in m.group(0).lower():
        print("DATA", m.group(0)[:300])
print("FORMS/LINKS", sorted(set(re.findall(r'(?:href|action|src)="(/[^"]*(?:voucher|kaart|bon|cart|winkel|listing|offer|product)[^"]*)"', html)))[:40])
