#!/usr/bin/env python3
"""
llm-cost-diff — gate de coût LLM pour la CI.

Lit llm-costs.json (les workloads du repo utilisateur), récupère le dataset
ouvert de l'index (all.json, CC BY 4.0), calcule la facture mensuelle estimée,
la compare au verrou llm-costs.lock.json commité, et échoue si la dérive
dépasse le seuil — un repricing provider ou un changement de modèle dans un
PR devient un diff visible et bloquant, comme un lockfile de dépendances.

Stdlib uniquement (aucune dépendance) : tourne tel quel sur tout runner CI.

Config (llm-costs.json) :
{
  "threshold_pct": 10,
  "workloads": [
    {"model": "openai:gpt-5.6-luna", "input_mtok": 200, "output_mtok": 15, "cache_pct": 60}
  ]
}

Usage :
  cost_diff.py check   # calcule, compare au lock, exit 1 si dérive > seuil
  cost_diff.py update  # (ré)écrit le lock avec la facture du jour
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

DATA_URL = os.environ.get("LLM_COST_INDEX_URL", "https://apipriceindex.com/api/all.json")
CONFIG = Path(os.environ.get("LLM_COST_CONFIG", "llm-costs.json"))
LOCK = Path(os.environ.get("LLM_COST_LOCK", "llm-costs.lock.json"))
# LLM_COST_STRICT=1 : une source injoignable fait échouer le check (défaut : fail-open —
# la panne de l'index ne doit JAMAIS casser la CI de l'utilisateur).
STRICT = os.environ.get("LLM_COST_STRICT", "") == "1"
DEFAULT_THRESHOLD_PCT = 10.0
STALE_DAYS = 14  # au-delà : l'index est suspect (pipeline amont mort ?), on avertit


def fetch_index() -> dict | None:
    """None si la source est injoignable ou illisible (le caller décide, cf. STRICT)."""
    req = urllib.request.Request(DATA_URL, headers={"User-Agent": "llm-cost-diff/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # réseau, HTTP, JSON — même traitement : source indisponible
        print(f"⚠ index injoignable ({DATA_URL}) : {e}", file=sys.stderr)
        return None


def warn_if_stale(index: dict) -> None:
    """as_of trop vieux = l'index tourne peut-être à vide — on vérifie quand même, mais on le dit."""
    as_of = index.get("as_of") or ""
    try:
        from datetime import datetime, timezone
        dt = datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age_d = (datetime.now(timezone.utc) - dt).days
        if age_d > STALE_DAYS:
            print(f"⚠ index daté du {as_of} ({age_d} j) — prix possiblement périmés, "
                  f"vérifie https://apipriceindex.com/health.json", file=sys.stderr)
    except ValueError:
        pass


def monthly_cost(w: dict, model: dict) -> float:
    p = model.get("pricing") or {}
    inp, out = p.get("input_per_mtok"), p.get("output_per_mtok")
    if inp is None or out is None:
        raise SystemExit(f"✗ {w['model']} : pas de prix dans l'index — retire-le ou vérifie l'id.")
    cached = p.get("cached_input_per_mtok")
    c = min(100.0, max(0.0, float(w.get("cache_pct", 0)))) / 100.0
    if cached is None:
        c = 0.0  # pas de prix caché publié → aucune remise inventée
    vin, vout = float(w.get("input_mtok", 0)), float(w.get("output_mtok", 0))
    return vin * (1 - c) * inp + vin * c * (cached if cached is not None else inp) + vout * out


def compute() -> dict | None:
    if not CONFIG.exists():
        raise SystemExit(f"✗ config introuvable : {CONFIG}")
    cfg = json.loads(CONFIG.read_text())
    index = fetch_index()
    if index is None:
        return None
    warn_if_stale(index)
    by_id = {m["id"]: m for m in index.get("models", [])}
    lines, total = [], 0.0
    for w in cfg.get("workloads", []):
        m = by_id.get(w["model"])
        if m is None:
            raise SystemExit(f"✗ {w['model']} absent de l'index ({DATA_URL}). "
                             f"Ids valides : voir https://apipriceindex.com/use-this-data/")
        cost = round(monthly_cost(w, m), 2)
        total += cost
        lines.append({"model": w["model"], "monthly_usd": cost,
                      "price_verified_at": (m.get("pricing") or {}).get("verified_at")})
    return {"index_as_of": index.get("as_of"), "total_monthly_usd": round(total, 2),
            "threshold_pct": float(cfg.get("threshold_pct", DEFAULT_THRESHOLD_PCT)),
            "workloads": lines}


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "check"
    cur = compute()
    if cur is None:
        # Fail-open : la panne de l'index n'est pas la faute de l'utilisateur.
        if STRICT:
            print("✗ index indisponible (mode strict) — check en échec.", file=sys.stderr)
            return 1
        print("⚠ index indisponible — check sauté (fail-open). "
              "LLM_COST_STRICT=1 pour en faire une erreur.")
        return 0
    print(f"Facture mensuelle estimée : ${cur['total_monthly_usd']}")
    for l in cur["workloads"]:
        print(f"  {l['model']:<38} ${l['monthly_usd']}")

    if cmd == "update":
        LOCK.write_text(json.dumps(cur, indent=2) + "\n")
        print(f"✓ lock écrit : {LOCK}")
        return 0

    if not LOCK.exists():
        print(f"⚠ pas de lock ({LOCK}) — lance `cost_diff.py update` et commite-le.")
        return 0
    prev = json.loads(LOCK.read_text())
    old_total = prev.get("total_monthly_usd", 0)
    if old_total <= 0:
        return 0
    delta_pct = (cur["total_monthly_usd"] - old_total) / old_total * 100
    print(f"Δ vs lock : {delta_pct:+.1f}% (lock ${old_total}, seuil ±{cur['threshold_pct']}%)")
    if abs(delta_pct) > cur["threshold_pct"]:
        print(f"✗ dérive de coût au-delà du seuil — vérifie le repricing puis "
              f"`cost_diff.py update` pour accepter.")
        return 1
    print("✓ dans le seuil.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
