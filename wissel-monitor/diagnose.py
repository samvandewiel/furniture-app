"""Tijdelijke diagnose: opmaak van de listings op een wissel.nl-merkpagina."""
import re
import urllib.request

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
req = urllib.request.Request("https://www.wissel.nl/kopen/cadeaubonnen-met-korting/coolblue", headers={"User-Agent": UA})
html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
html = re.sub(r"<(script|style|svg)\b.*?</\1>", "", html, flags=re.S)
html = re.sub(r'\sclass="[^"]*"', "", html)
html = re.sub(r"\s+", " ", html)
print("TOEVOEGEN", html.count("winkelwagen/toevoegen"))
print("DATA-ATTRS", sorted(set(re.findall(r"\s(data-[\w-]+)=", html))))
hits = [m.start() for m in re.finditer(r"winkelwagen/toevoegen", html)]
for n, pos in enumerate(hits[:3]):
    chunk = html[max(0, pos - 2500): pos + 1200]
    for i in range(0, len(chunk), 1800):
        print(f"ITEM{n}", chunk[i:i + 1800])
if not hits:
    i = html.find("Selecteer een waarde")
    chunk = html[i:i + 12000]
    for j in range(0, len(chunk), 1800):
        print("AFTER", chunk[j:j + 1800])
