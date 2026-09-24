"""Tijdelijke diagnose: opbouw van de Swappy-productpagina."""
import json
import re
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "nl-NL,nl;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, e.read().decode("utf-8", "replace")[:500]


status, final, html = get("https://www.swappy.nl/product/coolblue-cadeaubon-met-korting/")
print("PAGE", status, final, len(html))
print("GENERATOR", re.findall(r'<meta name="generator" content="([^"]+)"', html))
print("BODYCLASS", re.findall(r'<body[^>]*class="([^"]+)"', html)[:1])
for m in re.finditer(r'data-product_variations="([^"]+)"', html):
    raw = m.group(1).replace("&quot;", '"').replace("&amp;", "&")
    try:
        data = json.loads(raw)
        print("VARIATIONS", len(data))
        for v in data[:5]:
            print("  VAR", json.dumps({k: v.get(k) for k in ("variation_id", "attributes", "display_price", "display_regular_price", "is_in_stock", "max_qty", "sku")}))
    except Exception as e:
        print("VARIATIONS raw", raw[:1500], e)
print("DATA-ATTRS", sorted(set(re.findall(r"\s(data-[\w-]+)=", html)))[:80])
for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
    print("LDJSON", m.group(1)[:2500].replace("\n", " "))
body = re.sub(r"<(script|style|svg)\b.*?</\1>", "", html, flags=re.S)
body = re.sub(r'\s(class|style)="[^"]*"', "", body)
body = re.sub(r"\s+", " ", body)
idx = [m.start() for m in re.finditer("€|&euro;|&#8364;", body)]
print("EURO COUNT", len(idx))
if idx:
    start = max(0, idx[0] - 1500)
    chunk = body[start:start + 9000]
    for i in range(0, len(chunk), 1800):
        print("HTML", chunk[i:i + 1800])
for url in [
    "https://www.swappy.nl/wp-json/wc/store/v1/products?search=coolblue&per_page=5",
    "https://www.swappy.nl/wp-json/wc/store/v1/products?slug=coolblue-cadeaubon-met-korting",
    "https://www.swappy.nl/wp-json/",
]:
    time.sleep(2)
    s, f, t = get(url)
    print("API", s, url, t[:2500].replace("\n", " "))
