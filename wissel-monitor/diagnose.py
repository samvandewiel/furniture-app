"""Tijdelijke diagnose: cardswap.nl (Shopify?)."""
import json
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8", "replace")[:300]


for handle in ["apple", "bol-com", "bol-com-copy", "coolblue"]:
    url = f"https://www.cardswap.nl/collections/{handle}/products.json?limit=250"
    s, ct, body = get(url)
    print("URL", s, ct, url)
    if s != 200:
        print("  BODY", body[:300].replace("\n", " "))
        continue
    try:
        products = json.loads(body).get("products", [])
    except Exception as e:
        print("  NOT JSON", body[:300], e)
        continue
    print("  PRODUCTS", len(products))
    for p in products[:4]:
        print("  P", json.dumps({k: p.get(k) for k in ("id", "title", "handle", "vendor", "product_type", "tags", "published_at", "updated_at")}, ensure_ascii=False)[:600])
        for v in p.get("variants", [])[:4]:
            print("    V", json.dumps({k: v.get(k) for k in ("id", "title", "price", "compare_at_price", "available", "sku", "option1", "option2")}, ensure_ascii=False))
        print("    OPTIONS", json.dumps(p.get("options"), ensure_ascii=False)[:300])
    time.sleep(2)
