#!/usr/bin/env python3
"""Verwerkt de keuzes uit de workflow "Monitor instellingen" in settings.json.

Omgevingsvariabelen per platform: <BRON>_MIN_VALUE en <BRON>_MIN_DISCOUNT (bijv.
CARDSWAP_MIN_VALUE=25). "ongewijzigd" of leeg laat de huidige waarde staan. Print de nieuwe
drempels en zet changed=true/false in $GITHUB_OUTPUT.
"""
from __future__ import annotations

import json
import os
import sys

import monitor

UNCHANGED = {"", "ongewijzigd"}


def apply_choices(settings: dict, env: dict, sources) -> dict:
    updated = json.loads(json.dumps(settings))
    for source in sources:
        conf = updated.setdefault(source, {})
        for key in ("min_value", "min_discount"):
            choice = env.get(f"{source.upper()}_{key.upper()}", "").strip().lower()
            if choice in UNCHANGED:
                continue
            number = float(choice.replace(",", ".").rstrip("%").lstrip("€"))
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
