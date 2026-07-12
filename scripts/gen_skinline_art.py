#!/usr/bin/env python3
"""Regenerate webapp/skinline_art.json — splash-art wallpapers per
skinline, PER CHAMPION, so a card's banner can reflect who's actually
in the comp (Pool Party Rek'Sai's splash vs Pool Party Leona's splash).

Run this whenever Riot adds new skinlines/skins:

    python scripts/gen_skinline_art.py

Output shape (paths are relative to `base` to keep the file small):

    {
      "base": "https://raw.communitydragon.org/.../global/default/",
      "lines": {
        "Pool Party": {
          "_":        "assets/.../newest_uncentered.jpg",   # default
          "Rek'Sai":  "assets/.../reksai_uncentered_N.jpg",
          "Leona":    "assets/.../leona_uncentered_N.jpg",
          ...
        },
        ...
      }
    }

Season variants (e.g. "Star Guardian Season 3") merge to the base name
to match what the companion sends. Standard library only.
"""
import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "release"))
import league_skin_matcher as lsm  # for normalize_skinline

CDRAGON = ("https://raw.communitydragon.org/latest/plugins/"
           "rcp-be-lol-game-data/global/default/v1")
BASE = ("https://raw.communitydragon.org/latest/plugins/"
        "rcp-be-lol-game-data/global/default/")
OUT = REPO / "webapp" / "skinline_art.json"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "LSM/1"})
    return json.loads(urllib.request.urlopen(req, timeout=90).read())


def rel_path(path):
    """The CommunityDragon-served path relative to BASE (lowercased)."""
    if not path or "/lol-game-data/assets/" not in path:
        return None
    return path.split("/lol-game-data/assets/", 1)[1].lower()


def main():
    champ_name = {c["id"]: c["name"]
                  for c in get(f"{CDRAGON}/champion-summary.json")
                  if c.get("id", -1) > 0}
    skinlines = {s["id"]: s["name"]
                 for s in get(f"{CDRAGON}/skinlines.json")
                 if s.get("name", "").strip()}
    skins = get(f"{CDRAGON}/skins.json")

    # merged line -> {champ -> (skin_id, relpath)}; keep newest per champ
    lines = {}
    for sk in skins.values():
        if sk.get("isBase") or not sk.get("skinLines"):
            continue
        rel = (rel_path(sk.get("uncenteredSplashPath"))
               or rel_path(sk.get("splashPath")))
        if not rel:
            continue
        champ = champ_name.get(sk["id"] // 1000)
        if not champ:
            continue
        for ln in sk["skinLines"]:
            name = skinlines.get(ln["id"])
            if not name:
                continue
            merged = lsm.normalize_skinline(name)
            slot = lines.setdefault(merged, {})
            prev = slot.get(champ)
            if prev is None or sk["id"] > prev[0]:
                slot[champ] = (sk["id"], rel)

    out_lines = {}
    for name, champs in lines.items():
        # per-champion paths, plus "_" default = the newest skin overall
        newest = max(champs.values(), key=lambda v: v[0])[1]
        entry = {"_": newest}
        for champ, (_id, rel) in champs.items():
            entry[champ] = rel
        out_lines[name] = entry

    OUT.write_text(
        json.dumps({"base": BASE, "lines": dict(sorted(out_lines.items()))},
                   separators=(",", ":")),
        encoding="utf-8")
    n_champs = sum(len(v) - 1 for v in out_lines.values())
    print(f"wrote {len(out_lines)} skinlines, {n_champs} champion "
          f"wallpapers -> {OUT}")


if __name__ == "__main__":
    main()
