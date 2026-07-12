#!/usr/bin/env python3
"""Regenerate webapp/skinline_art.json — a map of

    { merged skinline name -> official splash-art wallpaper URL }

Run this whenever Riot adds new skinlines/skins:

    python scripts/gen_skinline_art.py

Picks the newest skin (highest id) in each skinline as its
representative wallpaper, preferring the wide uncentered splash. Season
variants (e.g. "Star Guardian Season 3") are merged to the base name to
match what the companion sends. Standard library only.
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
ASSET_BASE = ("https://raw.communitydragon.org/latest/plugins/"
              "rcp-be-lol-game-data/global/default/")
OUT = REPO / "webapp" / "skinline_art.json"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "LSM/1"})
    return json.loads(urllib.request.urlopen(req, timeout=90).read())


def asset_url(path):
    """CommunityDragon serves /lol-game-data/assets/<P> at
    plugins/.../global/default/<lowercased P>."""
    if not path or "/lol-game-data/assets/" not in path:
        return None
    return ASSET_BASE + path.split("/lol-game-data/assets/", 1)[1].lower()


def main():
    skinlines = {s["id"]: s["name"]
                 for s in get(f"{CDRAGON}/skinlines.json")
                 if s.get("name", "").strip()}
    skins = get(f"{CDRAGON}/skins.json")

    by_line = {}   # merged name -> [(skin_id, url), ...]
    for sk in skins.values():
        if sk.get("isBase") or not sk.get("skinLines"):
            continue
        url = (asset_url(sk.get("uncenteredSplashPath"))
               or asset_url(sk.get("splashPath")))
        if not url:
            continue
        for ln in sk["skinLines"]:
            name = skinlines.get(ln["id"])
            if name:
                by_line.setdefault(
                    lsm.normalize_skinline(name), []).append((sk["id"], url))

    art = {name: max(entries, key=lambda e: e[0])[1]
           for name, entries in by_line.items()}
    OUT.write_text(json.dumps(dict(sorted(art.items())), indent=0),
                   encoding="utf-8")
    print(f"wrote {len(art)} skinline wallpapers -> {OUT}")


if __name__ == "__main__":
    main()
