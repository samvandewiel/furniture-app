#!/usr/bin/env python3
"""Verwerkt de keuzes uit de workflow "Monitor instellingen" in settings.json.

Per platform en per drempel twee keuzes: het deel vóór de komma (<BRON>_MIN_VALUE=25) en
het deel erna (<BRON>_MIN_VALUE_DEC=",50"). Kies je alleen het hele getal, dan wordt het
precies dat getal (",0"); kies je alleen achter de komma, dan blijft het hele getal staan.
Beide "ongewijzigd" (of leeg) laat de drempel zoals hij is.
Print de nieuwe drempels en zet changed=true/false in $GITHUB_OUTPUT.
"""
from __future__ import annotations

import json
import os
import sys

import monitor

UNCHANGED = {"", "ongewijzigd"}


def parse_number(text: str) -> float:
    return float(text.replace(",", ".").rstrip("%").lstrip("€").strip())


def parse_decimals(text: str) -> float:
    """Deel achter de komma, bijv. ',50' -> 0.5 en ',9' -> 0.9."""
    digits = text.strip().lstrip(",.")
    if not digits.isdigit():
        raise ValueError(f"ongeldig deel achter de komma: {text!r}")
    return float("0." + digits)


def apply_choices(settings: dict, env: dict, sources, defaults: dict | None = None) -> dict:
    defaults = defaults or {"min_value": monitor.DEFAULT_MIN_VALUE, "min_discount": monitor.DEFAULT_MIN_DISCOUNT}
    updated = json.loads(json.dumps(settings))
    for source in sources:
        conf = updated.setdefault(source, {})
        for key in ("min_value", "min_discount"):
            name = f"{source.upper()}_{key.upper()}"
            whole = env.get(name, "").strip().lower()
            dec = env.get(f"{name}_DEC", "").strip().lower()
            if whole in UNCHANGED and dec in UNCHANGED:
                continue
            current = float(conf.get(key, defaults[key]))
            if dec in UNCHANGED:
                number = parse_number(whole)
            else:
                base = int(current) if whole in UNCHANGED else int(parse_number(whole))
                number = base + parse_decimals(dec)
            number = round(number, 2)
            conf[key] = int(number) if number.is_integer() else number
    return updated


def main() -> int:
    sources = list(monitor.SOURCE_NAMES)
    path = monitor.SETTINGS_FILE
    old = json.loads(path.read_text()) if path.exists() else {}
    new = apply_choices(old, dict(os.environ), sources)
    changed = new != old
    if changed:
        path.write_text(json.dumps(new, indent=1, sort_keys=True) + "\n")
    thresholds = monitor.thresholds_for(sources, new, monitor.DEFAULT_MIN_VALUE, monitor.DEFAULT_MIN_DISCOUNT)
    summary = monitor.describe_thresholds(thresholds)
    print(("Nieuwe" if changed else "Ongewijzigde") + " drempels:\n" + summary)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as out:
            out.write(f"changed={'true' if changed else 'false'}\n")
            out.write("summary<<EOF\n" + summary + "\nEOF\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
