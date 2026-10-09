# Wissel monitor

Checkt elke 2 minuten [wissel.nl](https://www.wissel.nl) en [cardswap.nl](https://www.cardswap.nl)
op nieuwe cadeaubonnen van
**Coolblue, Apple, MediaMarkt en Bol.com** met **meer dan 5% korting** en een
**waarde van minimaal €50**, en stuurt dan een pushmelding naar je iPhone.

Werkt zo: GitHub Actions draait `monitor.py`, die per merk de pagina
`wissel.nl/kopen/cadeaubonnen-met-korting/<merk>` uitleest. Elke listing heeft daar een code
als `v1-b75-n10000-s9300-exp27-03-01` (waarde €100, prijs €93, geldig t/m 01-03-2027).
Het script onthoudt welke deals je al hebt gezien en stuurt alleen nieuwe deals via
[ntfy](https://ntfy.sh) (gratis, geen account nodig). Tik je op de melding, dan open je de merkpagina.

Let op: listings met exact dezelfde waarde, prijs en vervaldatum vallen op wissel.nl samen.
Komt er zo'n identieke bon bij, dan krijg je daar geen aparte melding van.

**Cardswap** is een Shopify-winkel: het script haalt elk half uur de lijst met collecties op
(`cardswap.nl/collections.json`), kiest daaruit zelf de collecties voor de gevolgde merken
(de merken uit `settings.json`) en leest die via
`cardswap.nl/collections/<collectie>/products.json`. Komt er een nieuwe collectie bij,
bijvoorbeeld MediaMarkt, dan gaat die vanzelf mee. Daar is elke bon een eigen product
("Apple 50 euro" voor €46), dus elke nieuwe bon geeft een eigen melding, met een link
direct naar die bon. Een lege collectie (alles verkocht) is normaal.

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

### Merken en drempels (`settings.json`)

`wissel-monitor/settings.json` bepaalt per merk de drempels, en ook wélke merken gevolgd
worden. Elk merk in de lijst wordt op alle aangesloten sites gezocht (Wissel en Cardswap);
een nieuw merk wordt daar automatisch opgezocht.

```json
{
 "standaard": {"min_value": 50, "min_discount": 5},
 "merken": {
  "coolblue": {"naam": "Coolblue", "min_value": 49, "min_discount": 9.9}
 }
}
```

- `min_value`: minimale waarde van de bon in euro (mag met decimalen, bijv. `24.95`);
- `min_discount`: korting moet hóger zijn dan dit percentage (`4.9` = vanaf 5%);
- `standaard`: geldt voor een merk dat zelf geen waarde heeft.

Aanpassen gaat het makkelijkst door het aan Claude te vragen ("zet Bol.com op €25 en 7%"):
die past `settings.json` op `master` aan. De draaiende monitor leest het bestand bij elke
check opnieuw, dus het geldt binnen ~2 minuten, zonder herstart. Verlaag je een drempel, dan
krijg je ook meldingen voor bonnen die er al stonden en nu wél aan de drempel voldoen.

### Overige instellingen

Wil je testen zonder meldingen? Vink bij *Run workflow* de optie
"Alleen testen" aan; dan doet hij één check en staan de gevonden deals in de log.

**Hoe hij blijft draaien:** GitHub start geplande runs vaak uren te laat. Daarom draait één
run ongeveer 5,5 uur en checkt hij zelf elke 2 minuten; aan het eind start hij de volgende
run. Een cron elk uur vangt het op als die keten breekt. Start je handmatig een run terwijl
er al een loopt, dan wacht die in de wachtrij tot de lopende klaar is.

Onder *Settings → Secrets and variables → Actions → Variables* (optioneel):

| Variabele      | Standaard                          | Betekenis                          |
|----------------|------------------------------------|------------------------------------|
| `MIN_DISCOUNT` | `5`                                | korting-drempel (%) als settings.json geen `standaard` heeft |
| `MIN_VALUE`    | `50`                               | waarde-drempel (€) als settings.json geen `standaard` heeft |
| `CHECK_INTERVAL` | `120`                            | seconden tussen twee checks          |
| `SOURCES`      | `wissel,cardswap`                  | welke sites                          |
| `CARDSWAP_COLLECTIONS` | (leeg)                     | extra cardswap-collecties naast de automatisch gevonden (laatste deel van de URL) |

## Lokaal testen

```bash
cd wissel-monitor
DRY_RUN=1 STATE_FILE=/tmp/state.json python3 monitor.py   # print deals i.p.v. versturen
python3 -m unittest                                       # tests
```
