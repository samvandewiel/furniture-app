# Wissel monitor

Checkt elke 10 minuten [wissel.nl](https://www.wissel.nl) op nieuwe cadeaubonnen van
**Coolblue, Apple, MediaMarkt en Bol.com** met **meer dan 5% korting** en een
**waarde van minimaal €50**, en stuurt dan een pushmelding naar je iPhone.

Werkt zo: wissel.nl is een Shopify-winkel, dus alle producten staan in de openbare feed
`/products.json`. GitHub Actions draait `monitor.py`, die onthoudt welke deals je al hebt
gezien, en stuurt alleen nieuwe deals via [ntfy](https://ntfy.sh) (gratis, geen account nodig).
Tik je op de melding, dan open je de listing direct.

## Installeren (±5 minuten)

1. **iPhone:** installeer de app [ntfy](https://apps.apple.com/app/ntfy/id1625396347),
   tik op **+** en abonneer je op een zelfbedacht, moeilijk te raden topic,
   bijv. `wissel-sam-8f3k29x`. (Iedereen die het topic kent kan meelezen, dus maak het uniek.)
2. **GitHub:** ga naar *Settings → Secrets and variables → Actions* en voeg de secret
   `NTFY_TOPIC` toe met datzelfde topic.
3. Zet deze code op de **default branch** (`master`). GitHub draait geplande workflows alleen
   vanaf de default branch.
4. Ga naar *Actions → Wissel monitor → Run workflow* om hem meteen te testen. Bij de eerste
   run krijg je één melding met een overzicht van de deals die er nu zijn. Daarna krijg je
   alleen nog meldingen voor nieuwe deals.

## Instellingen aanpassen

Onder *Settings → Secrets and variables → Actions → Variables* (optioneel):

| Variabele      | Standaard                          | Betekenis                          |
|----------------|------------------------------------|------------------------------------|
| `MIN_DISCOUNT` | `5`                                | korting moet hóger zijn dan dit (%) |
| `MIN_VALUE`    | `50`                               | minimale waarde van de bon (€)      |
| `BRANDS`       | `coolblue,apple,mediamarkt,bol`    | merken om te volgen                 |

## Lokaal testen

```bash
cd wissel-monitor
DRY_RUN=1 STATE_FILE=/tmp/state.json python3 monitor.py   # print deals i.p.v. versturen
python3 -m unittest                                       # tests
```
