#!/usr/bin/env python3
"""
League Skin Matcher
===================
Find out which League of Legends skinlines you and your friends can ALL play
together (High Noon, Pool Party, Star Guardian, ...) without manually
comparing skin libraries.

How it works
------------
1. Each friend runs this file (needs Python 3.9+, nothing else — standard
   library only) and clicks "Export My Skins" while their League client is
   open and logged in. That saves a small JSON file of their owned skins,
   read from the client's local API. Nothing is uploaded anywhere.
2. One person collects the JSON files, opens this app, and clicks
   "Add Friend's Library..." for each file. Added friends are remembered
   between sessions, so this only has to happen once per friend.
3. The table shows every skinline, who owns skins in it, and highlights in
   green the lines where EVERYONE can play a *different* champion at once.

Skinline names/groupings are downloaded automatically from CommunityDragon
(community mirror of Riot's own game data) and cached locally for a week.

Command line (optional):
    python league_skin_matcher.py              # open the GUI
    python league_skin_matcher.py --export     # export without the GUI
    python league_skin_matcher.py --selftest   # run built-in logic tests
"""

import argparse 
import base64
import json
import os
import queue
import re
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

APP_NAME = "League Skin Matcher"
APP_VERSION = "1.0"
APP_AUTHOR = "StallionPrime"
EXPORT_APP_ID = "LeagueSkinMatcher"
EXPORT_VERSION = 1

CDRAGON_BASE = (
    "https://raw.communitydragon.org/latest/plugins/"
    "rcp-be-lol-game-data/global/default/v1"
)
CACHE_FILE = Path.home() / ".league_skin_matcher_cache.json"
CACHE_MAX_AGE_DAYS = 7

# Added friends are remembered here between sessions (auto-saved/loaded).
DATA_FILE = Path.home() / ".league_skin_matcher_friends.json"

MAX_PLAYERS = 5  # the comparison is built for one five-stack
CACHE_FORMAT = 4  # bump when the cached game-data layout changes

# ---- Team Maker: champion role (position) data --------------------------
ROLES = ("Top", "Jungle", "Mid", "Bot", "Support")
# Meraki Analytics publishes per-position play rates derived from Riot's
# own position data; used to know which lanes a champion actually plays.
MERAKI_RATES_URL = ("https://cdn.merakianalytics.com/riot/lol/resources/"
                    "latest/en-US/championrates.json")
MERAKI_POSITIONS = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid",
                    "BOTTOM": "Bot", "UTILITY": "Support"}
# Class tags from champion-summary.json -> roles they can flex to. Kept
# VERY generous on purpose — one champion works in multiple lanes (Yone
# top/mid/bot, mage supports, ADCs mid...). The solver still tries each
# champion's primary lane first, so these flexes are fallbacks, not
# defaults. Also the fallback when Meraki can't be fetched.
CLASS_ROLES = {"marksman": ["Bot", "Mid", "Top"],
               "mage": ["Mid", "Bot", "Support", "Top"],
               "support": ["Support", "Bot"],
               "tank": ["Top", "Support", "Jungle"],
               "fighter": ["Top", "Jungle", "Mid", "Bot"],
               "assassin": ["Mid", "Jungle", "Top", "Bot"]}

# Personal touch: champions that transcend the meta entirely.
# Rek'Sai plays everywhere. This is not negotiable.
ROLE_OVERRIDES = {"Rek'Sai": ["Jungle", "Top", "Mid", "Bot", "Support"]}

# 64x64 PNG app icon (stylized Rek'Sai), base64-embedded so the
# single file needs no companion assets at runtime.
APP_ICON_PNG_B64 = (
    "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"
    "h3v+L+NfAO5Q66cWAAAAAElFTkSuQmCC"
)

LOCKFILE_CANDIDATES = [
    Path(r"C:\Riot Games\League of Legends\lockfile"),
    Path(r"D:\Riot Games\League of Legends\lockfile"),
    Path(r"C:\Program Files\Riot Games\League of Legends\lockfile"),
    Path.home() / "Riot Games" / "League of Legends" / "lockfile",
    Path("/Applications/League of Legends.app/Contents/LoL/lockfile"),
]

# CommunityDragon splits some thematic lines by season/year (e.g. "Star
# Guardian Season 3"). For "let's all play Star Guardian" purposes those are
# the same line, so the GUI can merge them (on by default).
_SEASON_SUFFIX = re.compile(r"\s+Season\s+\d+$", re.IGNORECASE)


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------

def http_get_json(url, headers=None, insecure=False, timeout=30):
    # CommunityDragon 403s the default Python-urllib user agent
    all_headers = {"User-Agent": f"{EXPORT_APP_ID}/{EXPORT_VERSION}"}
    all_headers.update(headers or {})
    req = urllib.request.Request(url, headers=all_headers)
    ctx = None
    if insecure:  # the LCU uses a self-signed cert on 127.0.0.1
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize_skinline(name):
    return _SEASON_SUFFIX.sub("", name).strip()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Skinline theming: an emoji badge + swatch color per line.
# Keyed by the season-merged name; unknown/future lines fall through to
# keyword rules, then to the default.
# --------------------------------------------------------------------------

SKINLINE_STYLE = {
    "Academy": ("🎓", "#8d6e42"),
    "Aegis Frame": ("🛡", "#4f7bd9"),
    "Albion": ("⚔", "#9aa7b8"),
    "Anima Squad": ("🐾", "#d63384"),
    "Arcade: Battle Bosses": ("👾", "#b04ad9"),
    "Arcade: Heroes": ("🕹", "#d94ab0"),
    "Arcana": ("🃏", "#7d3fbf"),
    "Arcane": ("⚗", "#3f7fbf"),
    "Arcanists": ("🔮", "#8e44ad"),
    "Arclight": ("✨", "#f5d76e"),
    "Ashen Knights": ("♞", "#5d5d5d"),
    "Astronauts": ("🚀", "#5dade2"),
    "Battle Academia": ("📚", "#e67e22"),
    "Battle Queens": ("👑", "#c0392b"),
    "Battle of the God-Kings": ("⚡", "#b7950b"),
    "Battlecast": ("🤖", "#a04000"),
    "Bees!": ("🐝", "#f1c40f"),
    "Beta": ("🧪", "#7f8c8d"),
    "Bewitching": ("🦇", "#6c3483"),
    "Black Rose Group": ("🥀", "#6b2d5c"),
    "Blackfrost": ("❄", "#2e4053"),
    "Blood Moon": ("👹", "#922b21"),
    "Bloodstone": ("🩸", "#a93226"),
    "Broken Covenant": ("🪞", "#b03a5b"),
    "Cafe Cuties": ("☕", "#d35400"),
    "Cats Versus Dogs": ("🐱", "#f0932b"),
    "Challenger": ("⚔", "#af7ac5"),
    "Chosen of the Wolf": ("🐺", "#566573"),
    "Chronicle": ("📖", "#95a5a6"),
    "Collector's Edition": ("💎", "#7f8c8d"),
    "Commando": ("🔫", "#4d5656"),
    "Conqueror": ("⚔", "#b9770e"),
    "Cops and Robbers": ("🚨", "#2874a6"),
    "Cosmic": ("🌌", "#4a235a"),
    "Cottontail": ("🐰", "#d98cb3"),
    "Coven": ("🦉", "#3b2c4a"),
    "Crimson Elite": ("🔫", "#943126"),
    "Crystal Rose": ("🌹", "#d98cb3"),
    "Culinary Masters": ("🍜", "#ca6f1e"),
    "Curse of the Void": ("🕳", "#5b2c6f"),
    "Cyber Pop": ("💿", "#c39bd3"),
    "DJ": ("🎧", "#17a589"),
    "Dark Star": ("🌑", "#1b2631"),
    "Dawnbringer": ("☀", "#d4ac0d"),
    "Day Job": ("⚙", "#7e5109"),
    "Death Blossom": ("🥀", "#884ea0"),
    "Death Sworn": ("💀", "#34495e"),
    "Debonair": ("🎩", "#212f3c"),
    "Definitely Not": ("🎭", "#7f8c8d"),
    "Demacia Vice": ("🕶", "#d954a0"),
    "Demonic": ("😈", "#7b241c"),
    "Dreadknights": ("🏰", "#515a5a"),
    "Dreadnova": ("☄", "#6e2c00"),
    "Dumpling Darlings": ("🥟", "#e59866"),
    "Dunkmaster": ("🏀", "#dc7633"),
    "Eclipse": ("🌙", "#37474f"),
    "Elderwood": ("🌳", "#1e8449"),
    "Elementalist": ("🌈", "#af7ac5"),
    "Empyrean": ("⚡", "#7d3fd9"),
    "Eternum": ("🦂", "#641e16"),
    "Fables": ("📖", "#7d6608"),
    "Faerie Court": ("🦋", "#d35fb5"),
    "Fatemakers and Fatebreakers": ("🃏", "#5d6d7e"),
    "Fist Bumps and Festivities": ("🎆", "#c0392b"),
    "Flora Fatalis": ("🌺", "#6c3483"),
    "Food Fight": ("🍰", "#e74c3c"),
    "Forgotten Depths": ("🦈", "#154360"),
    "Freljord": ("❄", "#5dade2"),
    "Fright Night": ("👻", "#935116"),
    "Galactic": ("🪐", "#1f618d"),
    "Glacial": ("🧊", "#85c1e9"),
    "Goth": ("🖤", "#212121"),
    "Grand Reckoning": ("⚔", "#7d6608"),
    "Guardian of the Sands": ("🏺", "#b7950b"),
    "HEARTSTEEL": ("🖤", "#4a235a"),
    "Headhunter": ("💀", "#4d5656"),
    "Heartbreakers": ("💘", "#c2185b"),
    "Heartthrobs and Heartaches": ("💘", "#c2185b"),
    "Heavenscale": ("🐉", "#c0392b"),
    "Heavy Metal": ("🎸", "#424949"),
    "Hextech": ("⚙", "#148f77"),
    "High Noon": ("🤠", "#e07b1f"),
    "High Society": ("🎩", "#af8b5a"),
    "Highstakes": ("🃏", "#b7950b"),
    "Immortal Journey": ("🏮", "#cb4335"),
    "Infernal": ("🔥", "#cb4335"),
    "Inkshadow": ("🐲", "#2c3e50"),
    "Invaders": ("👾", "#4a235a"),
    "Justicar": ("🛡", "#2874a6"),
    "K/DA": ("🎤", "#d63fa0"),
    "Kaiju": ("🦖", "#196f3d"),
    "La Ilusión": ("🎭", "#e67e22"),
    "Lancer": ("♞", "#85929e"),
    "Legacy": ("📖", "#95a5a6"),
    "Luchador": ("🥊", "#c0392b"),
    "Mad Scientists": ("🧪", "#58d68d"),
    "Marauder": ("☠", "#641e16"),
    "Masked Justice": ("🎭", "#2874a6"),
    "Masque of the Black Rose": ("🥀", "#6b2d5c"),
    "Medieval": ("🏰", "#7e5109"),
    "Modern Mythos": ("🗿", "#5d6d7e"),
    "Monster Tamers": ("🐾", "#af601a"),
    "Mythmaker": ("🏮", "#b9770e"),
    "Nightbringer": ("🌙", "#34495e"),
    "Nightbringer and Dawnbringer": ("☀", "#6c3483"),
    "Ocean Song": ("🌊", "#16a085"),
    "Odyssey": ("🛸", "#ca6f1e"),
    "Omega Squad": ("🔫", "#4d5656"),
    "Omen of the Dark": ("🕸", "#4a235a"),
    "Order of the Lotus": ("🌷", "#c2519b"),
    "PAX": ("🎮", "#2874a6"),
    "PROJECT": ("🤖", "#e74c3c"),
    "Pandemonium": ("🎪", "#7b241c"),
    "Papercraft": ("✂", "#e59866"),
    "Petals of Spring": ("🌷", "#ec7063"),
    "Pharaoh": ("🏺", "#b7950b"),
    "Phoenixmancers": ("🐦", "#e74c3c"),
    "Piltover Customs": ("⚙", "#b9770e"),
    "Pool Party": ("🏖", "#1fc3c3"),
    "Popstar": ("🎤", "#ec407a"),
    "Porcelain": ("🏺", "#2874a6"),
    "Praetorian": ("🦅", "#78281f"),
    "Prehistoric Hunters": ("🦖", "#7e5109"),
    "Primal Ambush": ("🐍", "#196f3d"),
    "Primordian": ("🦖", "#4a235a"),
    "Program": ("💻", "#17a589"),
    "PsyOps": ("🧠", "#8e44ad"),
    "Pulsefire": ("⌛", "#2471a3"),
    "Rain Shepherd": ("☂", "#2471a3"),
    "Revenant Reign": ("💀", "#5b2c6f"),
    "Rift Hospital": ("🚑", "#c0392b"),
    "Rift Quest": ("🗡", "#27ae60"),
    "Riot": ("🎉", "#c0392b"),
    "Risen Legends": ("🏮", "#b9770e"),
    "Road Warrior": ("🏍", "#6e2c00"),
    "Sentinels of Light": ("✨", "#f4d03f"),
    "Shan Hai Scrolls": ("📜", "#b9770e"),
    "Shockblade": ("⚡", "#2471a3"),
    "Silver Age": ("📖", "#5d6d7e"),
    "Sinful Shores": ("☠", "#7b241c"),
    "Snow Day": ("⛄", "#85c1e9"),
    "Snow Moon": ("🌙", "#85a3c1"),
    "Snowdown Showdown": ("🎄", "#196f3d"),
    "Soccer Cup": ("⚽", "#229954"),
    "Soul Fighter": ("🥋", "#d35400"),
    "Soulstealer": ("👻", "#4a235a"),
    "Space Groove": ("🪩", "#af7ac5"),
    "Spirit Guard": ("🐻", "#2e86c1"),
    "Star Guardian": ("⭐", "#f2c94c"),
    "Stargazer": ("🌠", "#6c3483"),
    "Steel Legion": ("🛡", "#626567"),
    "Steel Valkyries": ("✈", "#85929e"),
    "Storybook": ("📖", "#a04000"),
    "Street Demons": ("🎨", "#c0392b"),
    "Sugar Rush": ("🍭", "#e91e63"),
    "Sunken Shadows": ("🦈", "#154360"),
    "Super Galaxy": ("🚀", "#e74c3c"),
    "Superfans": ("🎉", "#2874a6"),
    "Surprise Party": ("🎉", "#f06292"),
    "The Eternal Aspects": ("⛰", "#7d3c98"),
    "The Laws of Stone": ("🗿", "#5d6d7e"),
    "Three Honors": ("🏆", "#b7950b"),
    "Thunder Lord": ("⚡", "#2e4053"),
    "Toy Box": ("🧸", "#e59866"),
    "Traditional": ("📿", "#7e5109"),
    "Trick-or-Treat": ("🎃", "#e67e22"),
    "Triumphant": ("🏆", "#7f8c8d"),
    "True Damage": ("🎧", "#d4ac0d"),
    "True Warrior": ("⚔", "#7d6608"),
    "Urf the Manatee": ("🐟", "#45b39d"),
    "Vandal": ("🎸", "#616a6b"),
    "Victorious": ("🏆", "#b7950b"),
    "Visions of the Fallen": ("👁", "#4a235a"),
    "Warden": ("🛡", "#21618c"),
    "Warhounds": ("🐺", "#6e2c00"),
    "Winter Sports": ("🎿", "#2e86c1"),
    "Winter Wonder": ("⛄", "#a9cce3"),
    "Winterblessed": ("❄", "#7fb3d5"),
    "Withered Rose": ("🥀", "#884ea0"),
    "Woad": ("🏹", "#21618c"),
    "Wonders of the World": ("🌍", "#148f77"),
    "Worldbreaker": ("🌋", "#922b21"),
    "Zenith Games": ("🏆", "#d68910"),
    "Zombies VS Slayers": ("🧟", "#7b7d7d"),
    "arcticops": ("🧊", "#5d8aa8"),
}

# Keyword fallbacks (checked in order against the lowercased name) so new
# lines Riot adds later still get a sensible badge.
SKINLINE_RULES = [
    ("world champ", ("🏆", "#d4ac0d")),
    ("lunar", ("🧧", "#c0392b")),
    ("bilgewater", ("⚓", "#1a5276")),
    ("arcade", ("🕹", "#d94ab0")),
    ("crime city", ("🕵", "#7b241c")),
    ("pentakill", ("🎸", "#1c2833")),
    ("mecha", ("🤖", "#b03a2e")),
    ("spirit blossom", ("🌸", "#e8a0bf")),
    ("crystalis", ("💎", "#45b39d")),
    ("dragon", ("🐉", "#1e8449")),
    ("star", ("⭐", "#f2c94c")),
    ("moon", ("🌙", "#85a3c1")),
    ("heart", ("💘", "#c2185b")),
    ("snow", ("❄", "#85c1e9")),
    ("winter", ("❄", "#85c1e9")),
    ("frost", ("❄", "#85c1e9")),
    ("arctic", ("🧊", "#5d8aa8")),
    ("blood", ("🩸", "#a93226")),
    ("night", ("🌑", "#34495e")),
    ("shadow", ("🌑", "#34495e")),
    ("dark", ("🌑", "#1b2631")),
    ("fire", ("🔥", "#cb4335")),
    ("infernal", ("🔥", "#cb4335")),
    ("rose", ("🥀", "#884ea0")),
    ("blossom", ("🌸", "#e8a0bf")),
    ("king", ("👑", "#b7950b")),
    ("queen", ("👑", "#c0392b")),
    ("party", ("🎉", "#f06292")),
    ("ruin", ("🕯", "#148f77")),
    ("void", ("🕳", "#5b2c6f")),
]

DEFAULT_STYLE = ("🎨", "#9e9e9e")

# Chip backgrounds for the "Players loaded" bar — one per player, cycling.
CHIP_COLORS = ["#cfe8fc", "#d6f5d6", "#fde2cf", "#ecd9f7", "#fcd9e0",
               "#fff3c4", "#d2f0ef", "#e2f7cf", "#e8e0d2", "#dde3ea"]
CHIP_DISABLED = "#ebebeb"  # unticked players fade to gray


def style_for(line):
    """(emoji, swatch color) for a skinline name (any season variant)."""
    base = normalize_skinline(line)
    if base in SKINLINE_STYLE:
        return SKINLINE_STYLE[base]
    low = base.lower()
    for key, style in SKINLINE_RULES:
        if key in low:
            return style
    return DEFAULT_STYLE


# --------------------------------------------------------------------------
# Game data (skins -> skinlines -> names) from CommunityDragon, with cache
# --------------------------------------------------------------------------

class GameData:
    """Maps skin id -> (name, skinline ids), plus name lookups."""

    def __init__(self, skinline_names, champ_names, skins,
                 champ_positions=None):
        self.skinline_names = skinline_names  # {int: str}
        self.champ_names = champ_names        # {int: str}
        self.skins = skins                    # {int: (name, [skinline ids])}
        # {champion name: set of ROLES they can play}
        self.champ_positions = champ_positions or {}

    @classmethod
    def from_condensed(cls, c):
        champs = {int(k): v for k, v in c["champions"].items()}
        positions = {}
        for k, roles in (c.get("champion_positions") or {}).items():
            name = champs.get(int(k))
            if name:
                positions[name] = list(roles)  # ordered: primary role first
        return cls(
            {int(k): v for k, v in c["skinlines"].items()},
            champs,
            {int(k): (v[0], list(v[1])) for k, v in c["skins"].items()},
            positions,
        )

    def skinlines_for(self, skin_id):
        entry = self.skins.get(skin_id)
        if not entry:
            return []
        return [self.skinline_names[i] for i in entry[1]
                if i in self.skinline_names]


def _fetch_champion_positions(champs, status):
    """{champ id str: [roles]} from Meraki play rates + class-tag flexes.

    Role lists are ORDERED: the champion's primary lane (highest play
    rate) comes first, class-tag flexes last, so the team solver prefers
    sensible assignments and only reaches for off-meta flexes when it
    has to.
    """
    positions = {}
    try:
        status("Downloading champion role data...")
        rates = http_get_json(MERAKI_RATES_URL).get("data", {})
        for cid, pos_map in rates.items():
            if not isinstance(pos_map, dict):
                continue
            per = {MERAKI_POSITIONS[p]: (v or {}).get("playRate") or 0
                   for p, v in pos_map.items() if p in MERAKI_POSITIONS}
            if not per:
                continue
            best = max(per.values())
            roles = [r for r, rate in
                     sorted(per.items(), key=lambda kv: -kv[1])
                     if rate >= 5 or (best > 0 and rate == best)]
            if roles:
                positions[str(int(cid))] = roles
    except Exception:
        positions = {}  # Meraki is best-effort; classes still apply below
    for c in champs:
        if c.get("id", -1) <= 0:
            continue
        cid = str(c["id"])
        if c.get("name") in ROLE_OVERRIDES:
            positions[cid] = list(ROLE_OVERRIDES[c["name"]])
            continue
        flex = {r for tag in (c.get("roles") or [])
                for r in CLASS_ROLES.get(tag, [])}
        base = positions.get(cid, [])
        merged = base + [r for r in ROLES
                         if r in flex and r not in base]
        if merged:
            positions[cid] = merged
    return positions


def fetch_game_data(status=lambda msg: None):
    """Download and condense the CommunityDragon + role data files."""
    status("Downloading skinline data from CommunityDragon...")
    skinlines = http_get_json(f"{CDRAGON_BASE}/skinlines.json")
    champs = http_get_json(f"{CDRAGON_BASE}/champion-summary.json")
    status("Downloading skin catalog (~6 MB)...")
    skins = http_get_json(f"{CDRAGON_BASE}/skins.json")
    return {
        "format": CACHE_FORMAT,
        "fetched_at": utc_now_iso(),
        "skinlines": {str(s["id"]): s["name"] for s in skinlines
                      if s.get("name", "").strip()},
        "champions": {str(c["id"]): c["name"] for c in champs
                      if c.get("id", -1) > 0},
        "champion_positions": _fetch_champion_positions(champs, status),
        "skins": {
            str(s["id"]): [s.get("name", ""),
                           [ln["id"] for ln in (s.get("skinLines") or [])]]
            for s in skins.values() if not s.get("isBase")
        },
    }


def load_game_data(status=lambda msg: None, force_refresh=False):
    """Return GameData, preferring a <7 day old local cache."""
    cached = None
    if CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            cached = None
    if cached and not force_refresh \
            and cached.get("format") == CACHE_FORMAT:
        try:
            fetched = datetime.fromisoformat(cached["fetched_at"])
            age_days = (datetime.now(timezone.utc) - fetched).days
            if age_days < CACHE_MAX_AGE_DAYS:
                status("Loaded skinline data from local cache.")
                return GameData.from_condensed(cached)
        except Exception:
            pass
    try:
        condensed = fetch_game_data(status)
        try:
            CACHE_FILE.write_text(json.dumps(condensed), encoding="utf-8")
        except OSError:
            pass  # cache is best-effort
        status("Skinline data updated from CommunityDragon.")
        return GameData.from_condensed(condensed)
    except Exception as exc:
        if cached:
            status(f"Couldn't reach CommunityDragon ({exc}); "
                   "using older cached data.")
            return GameData.from_condensed(cached)
        raise RuntimeError(
            "Could not download skinline data from CommunityDragon and no "
            f"local cache exists yet. Check your internet connection. ({exc})"
        ) from exc


# --------------------------------------------------------------------------
# LCU (local League client API)
# --------------------------------------------------------------------------

class ExportError(RuntimeError):
    pass


class LCU:
    def __init__(self, port, token):
        self.port = port
        auth = base64.b64encode(f"riot:{token}".encode()).decode()
        self.headers = {"Authorization": f"Basic {auth}",
                        "Accept": "application/json"}

    def get(self, path, timeout=15):
        url = f"https://127.0.0.1:{self.port}{path}"
        return http_get_json(url, headers=self.headers, insecure=True,
                             timeout=timeout)

    def is_alive(self):
        try:
            self.get("/lol-summoner/v1/current-summoner", timeout=5)
            return True
        except Exception:
            return False


def _lcu_from_process():
    """Read port/token from the running LeagueClientUx process (Windows)."""
    if sys.platform != "win32":
        return None
    script = (
        "$ps = Get-CimInstance Win32_Process "
        "-Filter \"Name='LeagueClientUx.exe'\"; "
        "foreach ($p in @($ps)) { if ($p.CommandLine) "
        "{ [Console]::Out.WriteLine($p.CommandLine) } }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return None
    port = re.search(r"--app-port=(\d+)", out)
    token = re.search(r'--remoting-auth-token=([^"\s]+)', out)
    if port and token:
        return LCU(int(port.group(1)), token.group(1))
    return None


def _lcu_from_lockfile(path):
    try:
        parts = path.read_text(encoding="utf-8").strip().split(":")
        # format: name:pid:port:password:protocol
        return LCU(int(parts[2]), parts[3])
    except Exception:
        return None


def find_lcu(extra_lockfile=None):
    """Locate a live League client. Returns an LCU or None."""
    candidates = []
    proc = _lcu_from_process()
    if proc:
        candidates.append(proc)
    paths = list(LOCKFILE_CANDIDATES)
    if extra_lockfile:
        paths.insert(0, Path(extra_lockfile))
    for p in paths:
        if p.is_file():
            lcu = _lcu_from_lockfile(p)
            if lcu:
                candidates.append(lcu)
    for lcu in candidates:
        if lcu.is_alive():
            return lcu
    return None


def build_export(player_name, raw_skins, gamedata, mastery=None):
    """Turn the LCU skins-minimal payload into our shareable export dict."""
    records = []
    for s in raw_skins:
        if s.get("isBase"):
            continue
        if not (s.get("ownership") or {}).get("owned"):
            continue
        sid = int(s["id"])
        cid = int(s.get("championId") or sid // 1000)
        lines = gamedata.skinlines_for(sid) if gamedata else []
        champ = (gamedata.champ_names.get(cid) if gamedata else None) \
            or f"Champion {cid}"
        records.append({
            "id": sid,
            "name": s.get("name") or f"Skin {sid}",
            "championId": cid,
            "champion": champ,
            "skinlines": lines,
        })
    records.sort(key=lambda r: (r["champion"], r["name"]))
    return {
        "app": EXPORT_APP_ID,
        "version": EXPORT_VERSION,
        "player": player_name,
        "exported_at": utc_now_iso(),
        "skins": records,
        "mastery": mastery or {},  # champion id (str) -> mastery points
    }


def export_my_skins(gamedata, extra_lockfile=None):
    lcu = find_lcu(extra_lockfile)
    if lcu is None:
        raise ExportError(
            "No running League client found.\n\n"
            "Open the League of Legends client, log in, wait for it to "
            "finish loading, then try again.\n\n"
            "(If your client is running but wasn't detected, use "
            "'locate lockfile' when prompted — the lockfile lives in your "
            "League of Legends install folder.)"
        )
    try:
        summoner = lcu.get("/lol-summoner/v1/current-summoner")
    except Exception as exc:
        raise ExportError(f"Connected to the client but couldn't read the "
                          f"current summoner: {exc}") from exc
    game_name = summoner.get("gameName") or summoner.get("displayName") or ""
    tag = summoner.get("tagLine") or ""
    player = f"{game_name}#{tag}" if tag else (game_name or "Unknown")
    summoner_id = summoner.get("summonerId")
    try:
        raw = lcu.get(
            f"/lol-champions/v1/inventories/{summoner_id}/skins-minimal",
            timeout=30,
        )
    except Exception as exc:
        raise ExportError(f"Couldn't read the skin inventory from the "
                          f"client: {exc}") from exc
    if not isinstance(raw, list):
        raise ExportError(f"Unexpected response from the client: {raw!r}")
    # Champion mastery is a nice-to-have (used for the mastery filter);
    # the endpoint has moved between patches, so try a couple and move on.
    mastery = {}
    for path in ("/lol-champion-mastery/v1/local-player/champion-mastery",
                 f"/lol-collections/v1/inventories/{summoner_id}"
                 "/champion-mastery"):
        try:
            raw_mastery = lcu.get(path)
        except Exception:
            continue
        if isinstance(raw_mastery, list):
            for m in raw_mastery:
                cid = m.get("championId")
                pts = m.get("championPoints")
                if cid is not None and pts is not None:
                    mastery[str(int(cid))] = int(pts)
            break
    return build_export(player, raw, gamedata, mastery)


# --------------------------------------------------------------------------
# Libraries (one per player) and skinline matching
# --------------------------------------------------------------------------

class Library:
    def __init__(self, player, records, mastery=None):
        self.player = player
        self.records = records  # [{"id","name","champion","skinlines":[..]}]
        self.mastery = mastery or {}  # champion name -> mastery points


def parse_library(data, gamedata=None, display_name=None):
    """Parse an export dict into a Library.

    Skinline/champion names are re-resolved from current CommunityDragon
    data when possible so all players share one taxonomy, falling back to
    whatever names were embedded when the file was exported.
    """
    if not isinstance(data, dict) or not isinstance(data.get("skins"), list):
        raise ValueError("Not a League Skin Matcher export "
                         "(expected a JSON object with a 'skins' list).")
    player = display_name or str(data.get("player") or "Unknown player")
    records = []
    for s in data["skins"]:
        try:
            sid = int(s["id"])
        except (KeyError, TypeError, ValueError):
            continue
        cid = int(s.get("championId") or sid // 1000)
        lines = gamedata.skinlines_for(sid) if gamedata else []
        if not lines:
            lines = [str(x) for x in (s.get("skinlines") or []) if x]
        champ = (gamedata.champ_names.get(cid) if gamedata else None) \
            or s.get("champion") or f"Champion {cid}"
        name = s.get("name")
        if gamedata and sid in gamedata.skins and gamedata.skins[sid][0]:
            name = gamedata.skins[sid][0]
        records.append({
            "id": sid,
            "name": name or f"Skin {sid}",
            "champion": champ,
            "skinlines": lines,
        })
    mastery = {}
    for key, pts in (data.get("mastery") or {}).items():
        try:
            cid, pts = int(key), int(pts)
        except (TypeError, ValueError):
            continue
        champ = (gamedata.champ_names.get(cid) if gamedata else None) \
            or f"Champion {cid}"
        mastery[champ] = pts
    return Library(player, records, mastery)


def top_mastery_champs(lib, n):
    """The n champion names this player has the most mastery on.

    Returns None (= no restriction) for libraries exported before mastery
    data was included, so old files keep working.
    """
    if not lib.mastery:
        return None
    ranked = sorted(lib.mastery.items(), key=lambda kv: (-kv[1], kv[0]))
    return {champ for champ, _ in ranked[:n]}


def merge_libraries(name, libs):
    """Combine several accounts owned by ONE person into a single player.

    Skins pool together (deduped), mastery takes the highest value per
    champion across the accounts.
    """
    records = []
    seen = set()
    for lib in libs:
        for rec in lib.records:
            if rec["id"] not in seen:
                seen.add(rec["id"])
                records.append(rec)
    mastery = {}
    for lib in libs:
        for champ, pts in lib.mastery.items():
            mastery[champ] = max(mastery.get(champ, 0), pts)
    merged = Library(name, records, mastery)
    merged.merged_members = [lib.player for lib in libs]
    return merged


def build_matrix(libraries, merge_seasons=True, top_mastery=None,
                 mastery_scope=None):
    """{skinline name: {player: set of champion names}}

    With top_mastery=N, each player only contributes champions among
    their N highest-mastery ones. With mastery_scope set to a player
    name, that restriction applies to that player only ("my account")
    and everyone else contributes all their champions.
    """
    matrix = {}
    for lib in libraries:
        scoped = mastery_scope is None or lib.player == mastery_scope
        allowed = top_mastery_champs(lib, top_mastery) \
            if (top_mastery and scoped) else None
        for rec in lib.records:
            if allowed is not None and rec["champion"] not in allowed:
                continue
            for line in rec["skinlines"]:
                if merge_seasons:
                    line = normalize_skinline(line)
                matrix.setdefault(line, {}) \
                      .setdefault(lib.player, set()) \
                      .add(rec["champion"])
    return matrix


def find_assignment(player_champs, mastery_by_player=None):
    """Assign each player a DISTINCT champion from their own set.

    Standard bipartite matching (Kuhn's algorithm). Returns
    {player: champion} covering every player, or None if impossible.
    When mastery data is available, players are steered toward their
    highest-mastery champion among the possibilities.
    """
    mastery_by_player = mastery_by_player or {}
    players = list(player_champs)
    champ_owner = {}  # champion -> index into players

    def candidates(i):
        m = mastery_by_player.get(players[i], {})
        return sorted(player_champs[players[i]],
                      key=lambda c: (-m.get(c, 0), c))

    def try_assign(i, visited):
        for champ in candidates(i):
            if champ in visited:
                continue
            visited.add(champ)
            if champ not in champ_owner or \
                    try_assign(champ_owner[champ], visited):
                champ_owner[champ] = i
                return True
        return False

    for i in range(len(players)):
        if not try_assign(i, set()):
            return None
    return {players[i]: champ for champ, i in champ_owner.items()}


def row_matches(row, needle):
    """Text filter: hits on the skinline name OR any champion in the row."""
    if not needle:
        return True
    needle = needle.lower()
    if needle in row["line"].lower():
        return True
    return any(needle in champ.lower()
               for champs in row["per_player"].values()
               for champ in champs)


def build_rows(libraries, merge_seasons=True, top_mastery=None,
               mastery_scope=None):
    """One row per skinline, sorted with full matches first."""
    matrix = build_matrix(libraries, merge_seasons, top_mastery,
                          mastery_scope)
    mastery_by_player = {lib.player: lib.mastery for lib in libraries}
    total = len(libraries)
    rows = []
    for line, per_player in matrix.items():
        have = len(per_player)
        assignment = None
        clash = False
        if have == total and total > 0:
            assignment = find_assignment(per_player, mastery_by_player)
            clash = assignment is None
        rows.append({
            "line": line,
            "per_player": per_player,
            "have": have,
            "total": total,
            "full": assignment is not None,
            "clash": clash,
            "assignment": assignment,
        })
    # full matches first, then clashes, then by how many players have it
    rows.sort(key=lambda r: (
        0 if r["full"] else (1 if r["clash"] else 2),
        -r["have"],
        -sum(len(c) for c in r["per_player"].values()),
        r["line"].lower(),
    ))
    return rows


def find_team_comp(player_champs, champ_roles, mastery_by_player=None):
    """Assign every player a DISTINCT champion in a DISTINCT role.

    player_champs: {player: champions they own in this line}
    champ_roles:   {champion: ordered roles, primary lane first} —
                   champions with no role data are treated as flexible.
    Returns {player: (champion, role)} or None. With 4 players one of
    the five roles is left uncovered. Prefers high-mastery champions on
    their primary lanes.
    """
    mastery_by_player = mastery_by_player or {}
    players = sorted(player_champs,
                     key=lambda p: (len(player_champs[p]), p))
    used_champs = set()
    used_roles = set()
    result = {}

    def candidates(player):
        m = mastery_by_player.get(player, {})
        for champ in sorted(player_champs[player],
                            key=lambda c: (-m.get(c, 0), c)):
            if champ in used_champs:
                continue
            # ordered: the champion's primary role is tried first
            for role in (champ_roles.get(champ) or ROLES):
                if role not in used_roles:
                    yield champ, role

    def solve(i):
        if i == len(players):
            return True
        player = players[i]
        for champ, role in candidates(player):
            used_champs.add(champ)
            used_roles.add(role)
            result[player] = (champ, role)
            if solve(i + 1):
                return True
            used_champs.discard(champ)
            used_roles.discard(role)
            result.pop(player, None)
        return False

    return dict(result) if solve(0) else None


def build_team_rows(libraries, champ_roles, merge_seasons=True,
                    top_mastery=None, mastery_scope=None):
    """One row per skinline that ALL players own.

    Each row carries a best suggested comp plus role_pools: for every
    player and every role, the champions that player owns in this line
    that can play that role (sorted by their mastery).
    """
    matrix = build_matrix(libraries, merge_seasons, top_mastery,
                          mastery_scope)
    mastery_by_player = {lib.player: lib.mastery for lib in libraries}
    total = len(libraries)
    rows = []
    for line, per_player in matrix.items():
        if len(per_player) != total:
            continue
        comp = find_team_comp(per_player, champ_roles, mastery_by_player)
        role_pools = {}
        for player, champs in per_player.items():
            m = mastery_by_player.get(player, {})
            role_pools[player] = {
                role: sorted(
                    (c for c in champs
                     if role in (champ_roles.get(c) or ROLES)),
                    key=lambda c: (-m.get(c, 0), c))
                for role in ROLES
            }
        rows.append({"line": line, "per_player": per_player,
                     "comp": comp, "role_pools": role_pools})
    rows.sort(key=lambda r: (r["comp"] is None, r["line"].lower()))
    return rows


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

HELP_TEXT = f"""\
{APP_NAME} v{APP_VERSION} — made by {APP_AUTHOR}

GETTING STARTED
1. Open League and log in, then click "Export My Skins..." and save.
2. Swap files with friends; "Add Friend's Library..." to load theirs
   (max 5, remembered forever — re-add a newer file to update someone).
3. Green = everyone owns it AND can play different champions (click for
   the lineup). Yellow = owned by all, but the champions clash.

GOOD TO KNOW
- Add as many friends as you like; only 5 can be ticked in at once, so
  extras join benched. Untick/tick to pick tonight's squad; ✕ deletes.
- Own two accounts? "Merge Accounts..." pools their skins into one ⛓
  player (you can switch accounts as needed). ✕ on a merged chip just
  splits it apart — nothing is deleted.
- "me" marks your account — the mastery filter then narrows only you.
- The Filter box matches skinlines AND champions (try "Lux").
- Team Builder (4-5 ticked players): suggests a comp where everyone
  plays a different champion in a different role. Expand a row with ▸
  to see, per player, which of their champions fit each role. "Must
  include champion" (or the Talon button) keeps only lines where
  someone owns that champion and works them into the suggestion.
- Rek'Sai plays everywhere.

Nothing is uploaded anywhere — everything stays on your PC.
"""


def run_gui(preload=None, data_file=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    roster_path = Path(data_file) if data_file else DATA_FILE

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    root.title(APP_NAME)
    try:
        root.iconphoto(True, tk.PhotoImage(data=APP_ICON_PNG_B64))
    except Exception:
        pass  # icon is cosmetic; never block startup on it

    # Windows display scaling >100% grows the fonts but not our fixed
    # pixel sizes (window, column widths, row heights), which crams and
    # clips the tables. Measure the real DPI and scale EVERYTHING by it.
    # LSM_UI_SCALE=1.5 in the environment overrides it, just in case.
    try:
        ui_scale = root.winfo_fpixels("1i") / 96.0
    except Exception:
        ui_scale = 1.0
    try:
        ui_scale = float(os.environ.get("LSM_UI_SCALE") or ui_scale)
    except ValueError:
        pass
    ui_scale = max(1.0, ui_scale)

    def px(n):
        return int(n * ui_scale)

    win_w = min(px(1100), root.winfo_screenwidth() - 40)
    win_h = min(px(700), root.winfo_screenheight() - 80)
    root.geometry(f"{win_w}x{win_h}")
    root.minsize(min(px(820), win_w), min(px(520), win_h))

    # Treeview row height doesn't follow the font either — derive it from
    # the real font metrics, with a sane minimum.
    import tkinter.font as tkfont
    style = ttk.Style(root)
    try:
        linespace = tkfont.nametofont("TkDefaultFont").metrics("linespace")
    except Exception:
        linespace = 16
    row_height = max(px(24), linespace + 10)
    style.configure("Treeview", rowheight=row_height)
    swatch_px = max(14, row_height - 10)

    events = queue.Queue()
    state = {"gamedata": None, "libraries": [], "rows": [],
             "row_by_item": {}, "busy_export": False,
             "raw": {},      # player -> original export dict
             "enabled": {},  # player -> included in the comparison?
             "my_account": None,  # mastery filter applies only to them
             "chip_vars": {}, "chips": [], "chip_width": 0,
             "show_welcome": True,
             "merges": [],  # [{"name":…, "members":[player names]}]
             "swatches": {}}  # color -> PhotoImage (kept to avoid GC)

    def display_libs():
        """Libraries as shown: merged accounts appear as ONE player."""
        by_name = {l.player: l for l in state["libraries"]}
        # drop vanished members; dissolve groups that fall under 2
        state["merges"] = [
            {"name": g["name"],
             "members": [m for m in g["members"] if m in by_name]}
            for g in state["merges"]]
        state["merges"] = [g for g in state["merges"]
                           if len(g["members"]) >= 2]
        group_of = {m: g for g in state["merges"] for m in g["members"]}
        result = []
        emitted = set()
        for lib in state["libraries"]:
            group = group_of.get(lib.player)
            if group is None:
                result.append(lib)
            elif group["name"] not in emitted:
                emitted.add(group["name"])
                result.append(merge_libraries(
                    group["name"],
                    [by_name[m] for m in group["members"]]))
        return result

    def active_libs():
        return [l for l in display_libs()
                if state["enabled"].get(l.player, True)]

    # ---- toolbar -------------------------------------------------------
    toolbar = ttk.Frame(root, padding=(10, 8, 10, 4))
    toolbar.pack(fill="x")

    btn_export = ttk.Button(toolbar, text="Export My Skins...")
    btn_export.pack(side="left")
    btn_add = ttk.Button(toolbar, text="Add Friend's Library...")
    btn_add.pack(side="left", padx=(8, 0))
    btn_merge = ttk.Button(toolbar, text="Merge Accounts...")
    btn_merge.pack(side="left", padx=(8, 0))
    btn_help = ttk.Button(toolbar, text="Help",
                          command=lambda: messagebox.showinfo(
                              f"{APP_NAME} — Help", HELP_TEXT, parent=root))
    btn_help.pack(side="left", padx=(8, 0))

    only_full_var = tk.BooleanVar(value=False)
    merge_var = tk.BooleanVar(value=True)
    search_var = tk.StringVar()
    mastery_on_var = tk.BooleanVar(value=False)
    mastery_n_var = tk.IntVar(value=20)

    search_entry = ttk.Entry(toolbar, textvariable=search_var, width=26)
    search_entry.pack(side="right")
    ttk.Label(toolbar, text="Filter (skinline or champion):"
              ).pack(side="right", padx=(0, 4))

    toolbar2 = ttk.Frame(root, padding=(10, 0, 10, 4))
    toolbar2.pack(fill="x")
    ttk.Checkbutton(toolbar2, text="Merge seasonal lines",
                    variable=merge_var).pack(side="left")
    ttk.Checkbutton(toolbar2, text="Only lines everyone has",
                    variable=only_full_var).pack(side="left", padx=(14, 0))
    mastery_check = ttk.Checkbutton(toolbar2, text="Only each player's top",
                                    variable=mastery_on_var)
    mastery_check.pack(side="left", padx=(14, 0))
    mastery_spin = ttk.Spinbox(toolbar2, from_=1, to=170, width=4,
                               textvariable=mastery_n_var)
    mastery_spin.pack(side="left", padx=4)
    ttk.Label(toolbar2, text="mastery champions").pack(side="left")

    def update_mastery_label():
        who = state["my_account"] or "each player"
        mastery_check.configure(text=f"Only {who}'s top")

    # ---- players bar ---------------------------------------------------
    players_frame = ttk.LabelFrame(
        root, text="Players loaded ('me' = your account, for the mastery "
                   "filter)", padding=6)
    players_frame.pack(fill="x", padx=10, pady=(4, 4))
    players_inner = ttk.Frame(players_frame)
    players_inner.pack(fill="x")
    me_var = tk.StringVar(value="")

    # ---- main area: tabs -------------------------------------------------
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=10, pady=(0, 4))
    skin_tab = ttk.Frame(notebook, padding=(0, 4, 0, 0))
    notebook.add(skin_tab, text="  Skin Options  ")
    team_tab = ttk.Frame(notebook, padding=(0, 4, 0, 0))
    notebook.add(team_tab, text="  Team Builder  ")

    # ---- Skin Options tab: table + details ------------------------------
    paned = ttk.PanedWindow(skin_tab, orient="vertical")
    paned.pack(fill="both", expand=True)

    table_frame = ttk.Frame(paned)
    paned.add(table_frame, weight=3)

    tree = ttk.Treeview(table_frame, show="tree headings", selectmode="browse")
    vsb = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
    hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    table_frame.rowconfigure(0, weight=1)
    table_frame.columnconfigure(0, weight=1)

    tree.tag_configure("full", background="#d8f5d0")
    tree.tag_configure("clash", background="#fdf3c4")

    detail_frame = ttk.Frame(paned)
    paned.add(detail_frame, weight=1)
    detail = tk.Text(detail_frame, height=8, wrap="word", state="disabled",
                     relief="flat", background="#f7f7f7",
                     font=("Segoe UI", 10))
    dvsb = ttk.Scrollbar(detail_frame, orient="vertical",
                         command=detail.yview)
    detail.configure(yscrollcommand=dvsb.set)
    detail.pack(side="left", fill="both", expand=True)
    dvsb.pack(side="right", fill="y")

    # ---- Team Builder tab ------------------------------------------------
    team_bar = ttk.Frame(team_tab)
    team_bar.pack(fill="x", pady=(2, 2))
    ttk.Label(team_bar, text="Must include champion:").pack(side="left",
                                                            padx=(2, 4))
    team_lock_var = tk.StringVar()
    ttk.Entry(team_bar, textvariable=team_lock_var, width=18
              ).pack(side="left", padx=(0, 12))
    talon_var = tk.BooleanVar(value=False)

    def toggle_talon():
        if talon_var.get():
            team_lock_var.set("Talon")
        elif team_lock_var.get().strip().lower() == "talon":
            team_lock_var.set("")

    ttk.Checkbutton(team_bar, text="Talon only", variable=talon_var,
                    command=toggle_talon).pack(side="left")

    team_info_var = tk.StringVar()
    ttk.Label(team_tab, textvariable=team_info_var, padding=(2, 2, 2, 6),
              anchor="w", wraplength=px(1000)).pack(fill="x")
    team_paned = ttk.PanedWindow(team_tab, orient="vertical")
    team_paned.pack(fill="both", expand=True)

    team_frame = ttk.Frame(team_paned)
    team_paned.add(team_frame, weight=3)
    team_tree = ttk.Treeview(team_frame, show="tree headings",
                             selectmode="browse", columns=list(ROLES))
    team_vsb = ttk.Scrollbar(team_frame, orient="vertical",
                             command=team_tree.yview)
    team_hsb = ttk.Scrollbar(team_frame, orient="horizontal",
                             command=team_tree.xview)
    team_tree.configure(yscrollcommand=team_vsb.set,
                        xscrollcommand=team_hsb.set)
    team_tree.grid(row=0, column=0, sticky="nsew")
    team_vsb.grid(row=0, column=1, sticky="ns")
    team_hsb.grid(row=1, column=0, sticky="ew")
    team_frame.rowconfigure(0, weight=1)
    team_frame.columnconfigure(0, weight=1)
    team_tree.tag_configure("full", background="#d8f5d0")
    team_tree.tag_configure("clash", background="#fdf3c4")
    team_tree.tag_configure("alt", background="#eef7ee")

    team_detail_frame = ttk.Frame(team_paned)
    team_paned.add(team_detail_frame, weight=1)
    team_detail = tk.Text(team_detail_frame, height=9, wrap="word",
                          state="disabled", relief="flat",
                          background="#f7f7f7", font=("Segoe UI", 10))
    team_dvsb = ttk.Scrollbar(team_detail_frame, orient="vertical",
                              command=team_detail.yview)
    team_detail.configure(yscrollcommand=team_dvsb.set)
    team_detail.pack(side="left", fill="both", expand=True)
    team_dvsb.pack(side="right", fill="y")

    status_var = tk.StringVar(value="Starting...")
    ttk.Label(root, textvariable=status_var, padding=(10, 2),
              relief="sunken", anchor="w").pack(fill="x", side="bottom")

    def set_status(msg):
        status_var.set(msg)

    def set_detail(text):
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        detail.insert("1.0", text)
        detail.configure(state="disabled")

    def set_team_detail(text):
        team_detail.configure(state="normal")
        team_detail.delete("1.0", "end")
        team_detail.insert("1.0", text)
        team_detail.configure(state="disabled")

    def current_top_mastery():
        if not mastery_on_var.get():
            return None
        try:
            return max(1, int(mastery_n_var.get()))
        except Exception:
            return 20

    # ---- players bar refresh -------------------------------------------
    def remove_player(name):
        state["libraries"] = [l for l in state["libraries"]
                              if l.player != name]
        state["raw"].pop(name, None)
        state["enabled"].pop(name, None)
        state["chip_vars"].pop(name, None)
        if state["my_account"] == name:
            state["my_account"] = None
            me_var.set("")
            update_mastery_label()
        write_roster()
        refresh_players()
        refresh_table()
        set_status(f"Removed {name} and their data.")

    def toggle_player(name):
        state["enabled"][name] = state["chip_vars"][name].get()
        write_roster()
        refresh_players()  # recolor the chip (gray when sitting out)
        refresh_table()
        verb = "back in" if state["enabled"][name] else "sitting out"
        set_status(f"{name} is {verb} (data kept — untick/tick anytime; "
                   "✕ deletes them).")

    def on_me_change():
        clicked = me_var.get()
        if clicked == state["my_account"]:  # clicking again unmarks
            state["my_account"] = None
            me_var.set("")
        else:
            state["my_account"] = clicked
        update_mastery_label()
        write_roster()
        refresh_table()
        if state["my_account"]:
            set_status(f"{state['my_account']} marked as your account — "
                       "the mastery filter now narrows only their "
                       "champions (click 'me' again to unmark).")
        else:
            set_status("No account marked as yours — the mastery filter "
                       "applies to everyone.")

    def reflow_chips(width=None):
        """Lay chips out left-to-right, wrapping to new rows as needed."""
        chips = state["chips"]
        if not chips:
            return
        if width is None:
            width = players_inner.winfo_width()
        if width <= 1:  # not measured yet — try again shortly
            root.after(50, reflow_chips)
            return
        for chip in chips:
            chip.grid_forget()
        x = row = col = 0
        for chip in chips:
            w = chip.winfo_reqwidth() + 8
            if col > 0 and x + w > width:
                row, col, x = row + 1, 0, 0
            chip.grid(row=row, column=col, sticky="w",
                      padx=(0, 8), pady=2)
            col += 1
            x += w

    def on_players_resize(event):
        if abs(event.width - state["chip_width"]) > 2:
            state["chip_width"] = event.width
            reflow_chips(event.width)

    players_inner.bind("<Configure>", on_players_resize)

    def unmerge(name):
        state["merges"] = [g for g in state["merges"]
                           if g["name"] != name]
        state["enabled"].pop(name, None)
        state["chip_vars"].pop(name, None)
        if state["my_account"] == name:
            state["my_account"] = None
            me_var.set("")
            update_mastery_label()
        write_roster()
        refresh_players()
        refresh_table()
        set_status(f'Split "{name}" back into separate profiles '
                   "(no data was deleted).")

    def refresh_players():
        for child in players_inner.winfo_children():
            child.destroy()
        state["chip_vars"] = {}
        state["chips"] = []
        libs = display_libs()
        if not libs:
            ttk.Label(players_inner,
                      text="No libraries loaded yet — export your own or "
                           "add a friend's JSON file.").grid(row=0, column=0,
                                                             sticky="w")
            return
        for i, lib in enumerate(libs):
            enabled = state["enabled"].get(lib.player, True)
            is_merged = getattr(lib, "merged_members", None)
            bg = CHIP_COLORS[i % len(CHIP_COLORS)] if enabled \
                else CHIP_DISABLED
            # outer = colored tab + a separate "me" radio beside it, so a
            # missed click near "me" can't toggle the tab itself
            outer = ttk.Frame(players_inner)
            chip = tk.Frame(outer, background=bg, bd=1, relief="solid")
            chip.pack(side="left")
            var = tk.BooleanVar(value=enabled)
            state["chip_vars"][lib.player] = var
            marker = "⛓ " if is_merged else ""
            tk.Checkbutton(
                chip,
                text=f"{marker}{lib.player} — {len(lib.records)} skins",
                variable=var,
                command=lambda n=lib.player: toggle_player(n),
                background=bg, activebackground=bg,
                highlightthickness=0, bd=0,
                font=("Segoe UI", 9, "bold"),
            ).pack(side="left", padx=(6, 0), pady=3)
            close_cmd = (lambda n=lib.player: unmerge(n)) if is_merged \
                else (lambda n=lib.player: remove_player(n))
            tk.Button(chip, text="✕", command=close_cmd,
                      background=bg, activebackground=bg,
                      relief="flat", bd=0, padx=5,
                      font=("Segoe UI", 9),
                      ).pack(side="left", padx=(2, 4), pady=1)
            ttk.Radiobutton(outer, text="me", value=lib.player,
                            variable=me_var, command=on_me_change
                            ).pack(side="left", padx=(4, 0))
            state["chips"].append(outer)
        players_inner.update_idletasks()
        reflow_chips()

    def swatch(color):
        """A small solid-color square image used as the skinline badge."""
        img = state["swatches"].get(color)
        if img is None:
            img = tk.PhotoImage(width=swatch_px, height=swatch_px)
            img.put(color, to=(2, 2, swatch_px - 1, swatch_px - 1))
            state["swatches"][color] = img
        return img

    # ---- table refresh ---------------------------------------------------
    def refresh_skin_table(*_):
        libs = active_libs()
        players = [l.player for l in libs]
        tree.delete(*tree.get_children())
        state["row_by_item"] = {}

        tree.heading("#0", text="Skinline")
        tree.column("#0", width=px(250), minwidth=px(180), stretch=False)
        cols = ["match"] + players
        tree.configure(columns=cols)
        tree.heading("match", text="Match")
        tree.column("match", width=px(80), minwidth=px(70),
                    anchor="center", stretch=False)
        for p in players:
            tree.heading(p, text=p)
            tree.column(p, width=px(200), minwidth=px(120), stretch=True)

        if not libs:
            if state["libraries"]:
                msg = ("All players are unticked — tick at least one "
                       "checkbox above to compare.")
            else:
                msg = "Load some libraries to see skinline matches here."
            set_detail(msg)
            set_status(msg)
            return

        rows = build_rows(libs, merge_seasons=merge_var.get(),
                          top_mastery=current_top_mastery(),
                          mastery_scope=state["my_account"])
        state["rows"] = rows
        mastery_by_player = {l.player: l.mastery for l in libs}
        needle = search_var.get().strip().lower()
        full_count = 0
        shown = 0
        for row in rows:
            if not row_matches(row, needle):
                continue
            if only_full_var.get() and not (row["full"] or row["clash"]):
                continue
            if row["full"]:
                match_txt = f"✓ {row['have']}/{row['total']}"
                tag = "full"
                full_count += 1
            elif row["clash"]:
                match_txt = f"⚠ {row['have']}/{row['total']}"
                tag = "clash"
            else:
                match_txt = f"{row['have']}/{row['total']}"
                tag = ""
            values = [match_txt]
            for p in players:
                m = mastery_by_player.get(p, {})
                champs = sorted(row["per_player"].get(p, ()),
                                key=lambda c: (-m.get(c, 0), c))
                values.append(", ".join(champs))
            emoji, color = style_for(row["line"])
            item = tree.insert("", "end", text=f"{emoji} {row['line']}",
                               image=swatch(color), values=values,
                               tags=(tag,) if tag else ())
            state["row_by_item"][item] = row
            shown += 1
        total_full = sum(1 for r in rows if r["full"])
        set_status(
            f"{shown} skinlines shown ({total_full} playable by all "
            f"{len(libs)} player{'s' if len(libs) != 1 else ''}). "
            "Click a row for details."
        )
        set_detail("Click a skinline row to see full champion lists and a "
                   "suggested lineup.")

    def on_select(_event):
        sel = tree.selection()
        if not sel:
            return
        row = state["row_by_item"].get(sel[0])
        if not row:
            return
        libs = active_libs()
        mastery_by_player = {l.player: l.mastery for l in libs}

        def champ_label(player, champ):
            pts = mastery_by_player.get(player, {}).get(champ)
            return f"{champ} ({pts:,} mastery)" if pts else champ

        lines = [f"{style_for(row['line'])[0]} {row['line']}"]
        if row["full"]:
            lines.append("Everyone can play it! Suggested lineup:")
            for player, champ in sorted(row["assignment"].items()):
                lines.append(f"    {player}  →  {champ_label(player, champ)}")
        elif row["clash"]:
            lines.append("Everyone owns this line, but not on enough "
                         "different champions — no way for all of you to "
                         "play it at the same time without sharing a pick.")
        else:
            missing = [l.player for l in libs
                       if l.player not in row["per_player"]]
            lines.append(f"Owned by {row['have']} of {row['total']} players. "
                         f"Missing: {', '.join(missing)}")
        lines.append("")
        for lib in libs:
            champs = sorted(row["per_player"].get(lib.player, ()),
                            key=lambda c: (-lib.mastery.get(c, 0), c))
            if champs:
                labeled = [champ_label(lib.player, c) for c in champs]
                lines.append(f"{lib.player}: {', '.join(labeled)}")
            else:
                lines.append(f"{lib.player}: (none)")
        set_detail("\n".join(lines))

    tree.bind("<<TreeviewSelect>>", on_select)

    # ---- Team Builder refresh ----------------------------------------------
    def refresh_team(*_):
        team_tree.delete(*team_tree.get_children())
        state["team_rows"] = {}
        team_tree.heading("#0", text="Skinline")
        team_tree.column("#0", width=px(240), minwidth=px(180),
                         stretch=False)
        for role in ROLES:
            team_tree.heading(role, text=role)
            team_tree.column(role, width=px(175), minwidth=px(120),
                             stretch=True)
        libs = active_libs()
        set_team_detail("Click a row to see the full comp and everyone's "
                        "champion pools.")
        if state["gamedata"] is None:
            team_info_var.set("Still downloading game data — one moment...")
            return
        if len(libs) < 4 or len(libs) > MAX_PLAYERS:
            team_info_var.set(
                f"Team Builder needs 4 or 5 ticked players (you have "
                f"{len(libs)} ticked — use the checkboxes above to pick "
                "tonight's squad). It finds skinlines where your whole "
                "group can queue up with a different champion in a "
                "different role (Top / Jungle / Mid / Bot / Support).")
            return
        rows = build_team_rows(libs, state["gamedata"].champ_positions,
                               merge_seasons=merge_var.get(),
                               top_mastery=current_top_mastery(),
                               mastery_scope=state["my_account"])

        def comp_values(comp):
            by_role = {role: (p, champ)
                       for p, (champ, role) in comp.items()}
            values = []
            for role in ROLES:
                if role in by_role:
                    p, champ = by_role[role]
                    values.append(f"{champ}  ({p})")
                else:
                    values.append("—")
            return values

        gd_roles = state["gamedata"].champ_positions
        mastery_by_player = {l.player: l.mastery for l in libs}
        needle = search_var.get().strip().lower()
        lock = team_lock_var.get().strip().lower()
        comp_count = 0
        shown = 0
        for row in rows:
            if not row_matches(row, needle):
                continue
            comp = row["comp"]
            if lock:
                owners = [(p, c)
                          for p, champs in row["per_player"].items()
                          for c in champs if lock in c.lower()]
                if not owners:
                    continue  # nobody owns the locked champion here
                # pin the locked champion into the suggested comp
                comp = None
                for player, champ in owners:
                    pinned_pool = dict(row["per_player"])
                    pinned_pool[player] = [champ]
                    comp = find_team_comp(pinned_pool, gd_roles,
                                          mastery_by_player)
                    if comp:
                        break
            emoji, color = style_for(row["line"])
            tag = "full" if comp else "clash"
            if comp:
                comp_count += 1
            item = team_tree.insert(
                "", "end", text=f"{emoji} {row['line']}",
                image=swatch(color),
                values=comp_values(comp) if comp else [""] * len(ROLES),
                tags=(tag,))
            state["team_rows"][item] = (row, comp)
            for lib in libs:
                pools = row["role_pools"].get(lib.player, {})
                vals = [", ".join(pools.get(role, [])) or "—"
                        for role in ROLES]
                child = team_tree.insert(item, "end", text=lib.player,
                                         values=vals, tags=("alt",))
                state["team_rows"][child] = (row, comp)
            shown += 1
        note = " With 4 players, one role stays empty (—)." \
            if len(libs) == 4 else ""
        if lock:
            note += (f' Showing only lines where someone owns '
                     f'"{team_lock_var.get().strip()}" — the suggested '
                     f'comp includes them whenever possible.')
        team_info_var.set(
            f"{comp_count} of {shown} skinline(s) shown give all "
            f"{len(libs)} of you a full role split (suggested comp on the "
            f"row; click ▸ to see who can play what in every role; "
            f"yellow = everyone owns the line but there's no clean "
            f"split).{note}")

    def on_team_select(_event):
        sel = team_tree.selection()
        entry = state["team_rows"].get(sel[0]) if sel else None
        if not entry:
            return
        row, comp = entry
        gd = state["gamedata"]
        mastery_by_player = {l.player: l.mastery for l in active_libs()}
        lines = [f"{style_for(row['line'])[0]} {row['line']}"]
        if comp:
            lines.append("This option:")
            by_role = {role: (p, c)
                       for p, (c, role) in comp.items()}
            for role in ROLES:
                if role in by_role:
                    p, champ = by_role[role]
                    pts = mastery_by_player.get(p, {}).get(champ)
                    extra = f"  ({pts:,} mastery)" if pts else ""
                    lines.append(f"    {role:<9} {p}  →  {champ}{extra}")
                else:
                    lines.append(f"    {role:<9} (left empty)")
        else:
            lines.append("Everyone owns this line, but there's no way to "
                         "give each player a different champion in a "
                         "different role.")
        lines.append("")
        lines.append("Champion pools (playable roles):")
        for lib in active_libs():
            parts = []
            for champ in sorted(row["per_player"].get(lib.player, ())):
                roles = (gd.champ_positions.get(champ) if gd else None) \
                    or ROLES
                parts.append(f"{champ} [{'/'.join(roles)}]")
            lines.append(f"{lib.player}: {', '.join(parts) or '(none)'}")
        set_team_detail("\n".join(lines))

    team_tree.bind("<<TreeviewSelect>>", on_team_select)

    def refresh_table(*_):
        refresh_skin_table()
        refresh_team()

    # ---- adding / exporting libraries ----------------------------------
    def write_roster():
        try:
            payload = {"version": 1,
                       "my_account": state["my_account"],
                       "show_welcome": bool(state.get("show_welcome",
                                                      True)),
                       "merges": [
                           {"name": g["name"], "members": g["members"],
                            "enabled": bool(state["enabled"].get(
                                g["name"], True))}
                           for g in state["merges"]],
                       "friends": [
                {"name": lib.player, "data": state["raw"][lib.player],
                 "enabled": bool(state["enabled"].get(lib.player, True))}
                for lib in state["libraries"] if lib.player in state["raw"]
            ]}
            roster_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            set_status(f"Warning: couldn't save your friend list ({exc}).")

    def add_library_from_data(data, name=None, persist=True, enabled=True,
                              enforce_limit=True):
        lib = parse_library(data, state["gamedata"], display_name=name)
        names = {l.player for l in state["libraries"]}
        benched = False
        if enforce_limit and lib.player not in names:
            active = sum(1 for l in display_libs()
                         if state["enabled"].get(l.player, True))
            if active >= MAX_PLAYERS:
                enabled = False  # full squad already ticked — join benched
                benched = True
        existing = [l for l in state["libraries"] if l.player == lib.player]
        if existing:
            state["libraries"] = [l for l in state["libraries"]
                                  if l.player != lib.player]
            set_status(f"Replaced {lib.player}'s library.")
        state["libraries"].append(lib)
        state["raw"][lib.player] = data
        state["enabled"][lib.player] = enabled
        refresh_players()
        refresh_table()
        if persist:
            write_roster()
        if benched:
            set_status(f"{lib.player} added, but unticked — {MAX_PLAYERS} "
                       "players are already in the comparison. Tick them "
                       "in whenever you like.")
        return lib

    def load_roster():
        if not roster_path.is_file():
            return
        try:
            payload = json.loads(roster_path.read_text(encoding="utf-8"))
            for entry in payload.get("friends", []):
                add_library_from_data(entry["data"],
                                      name=entry.get("name"),
                                      persist=False,
                                      enabled=bool(entry.get("enabled",
                                                             True)),
                                      enforce_limit=False)
            state["show_welcome"] = bool(payload.get("show_welcome", True))
            loaded = {l.player for l in state["libraries"]}
            state["merges"] = []
            for g in payload.get("merges", []):
                members = [m for m in g.get("members", [])
                           if m in loaded]
                if g.get("name") and len(members) >= 2:
                    state["merges"].append({"name": g["name"],
                                            "members": members})
                    state["enabled"][g["name"]] = bool(
                        g.get("enabled", True))
            if state["merges"]:
                refresh_players()
                refresh_table()
            mine = payload.get("my_account")
            if mine and any(l.player == mine for l in display_libs()):
                state["my_account"] = mine
                me_var.set(mine)
                update_mastery_label()
            if state["libraries"]:
                set_status(f"Welcome back — remembered "
                           f"{len(state['libraries'])} player(s).")
        except Exception as exc:
            set_status(f"Couldn't read the saved friend list ({exc}).")

    def merge_accounts():
        in_merge = {m for g in state["merges"] for m in g["members"]}
        candidates = [l.player for l in state["libraries"]
                      if l.player not in in_merge]
        if len(candidates) < 2:
            messagebox.showinfo(
                APP_NAME, "You need at least two unmerged profiles loaded "
                "to merge (merging is for accounts that belong to the "
                "same person).", parent=root)
            return
        win = tk.Toplevel(root)
        win.title("Merge accounts")
        win.transient(root)
        win.grab_set()
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, justify="left", text=(
            "Combine accounts that belong to ONE person.\n"
            "Their skins pool together (you can switch accounts as "
            "needed):")).pack(anchor="w", pady=(0, 6))
        picks = {}
        for name in candidates:
            v = tk.BooleanVar(value=False)
            picks[name] = v
            ttk.Checkbutton(frame, text=name, variable=v
                            ).pack(anchor="w", padx=10)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="Merged name:").pack(side="left")
        name_var = tk.StringVar()
        ttk.Entry(row, textvariable=name_var, width=26
                  ).pack(side="left", padx=6)
        ttk.Label(row, text="(blank = automatic)").pack(side="left")

        def do_merge():
            members = [n for n in candidates if picks[n].get()]
            if len(members) < 2:
                messagebox.showwarning(
                    APP_NAME, "Pick at least two profiles to merge.",
                    parent=win)
                return
            merged_name = name_var.get().strip() or " + ".join(
                m.split("#")[0] for m in members)
            taken = {l.player for l in state["libraries"]} \
                | {g["name"] for g in state["merges"]}
            if merged_name in taken:
                messagebox.showwarning(
                    APP_NAME, f'The name "{merged_name}" is already in '
                    "use — pick another.", parent=win)
                return
            state["merges"].append({"name": merged_name,
                                    "members": members})
            state["enabled"][merged_name] = True
            write_roster()
            refresh_players()
            refresh_table()
            set_status(f'Merged {", ".join(members)} into '
                       f'"{merged_name}". The ✕ on its chip splits them '
                       "apart again (nothing gets deleted).")
            win.destroy()

        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Merge", command=do_merge
                   ).pack(side="left")
        ttk.Button(btns, text="Cancel", command=win.destroy
                   ).pack(side="left", padx=8)

    def add_files():
        paths = filedialog.askopenfilenames(
            parent=root, title="Add skin library files",
            filetypes=[("Skin library JSON", "*.json"),
                       ("All files", "*.*")])
        errors = []
        added = []
        for path in paths:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                default = str(data.get("player")
                              or Path(path).stem.replace("_skins", ""))
                name = simpledialog.askstring(
                    APP_NAME,
                    f"Whose library is this?\n({Path(path).name})",
                    initialvalue=default, parent=root)
                if name is None:
                    continue  # cancelled — skip this file
                lib = add_library_from_data(data,
                                            name=name.strip() or default)
                added.append(lib.player)
            except Exception as exc:
                errors.append(f"{Path(path).name}: {exc}")
        if added:
            set_status(f"Loaded and remembered: {', '.join(added)}")
        if errors:
            messagebox.showerror(
                "Some files couldn't be loaded", "\n\n".join(errors),
                parent=root)

    def do_export():
        if state["busy_export"]:
            return
        if state["gamedata"] is None:
            messagebox.showinfo(
                APP_NAME,
                "Still downloading skinline data — try again in a moment.",
                parent=root)
            return
        state["busy_export"] = True
        btn_export.configure(state="disabled")
        set_status("Looking for your League client...")

        def worker():
            try:
                export = export_my_skins(state["gamedata"])
                events.put(("export_ok", export))
            except ExportError as exc:
                events.put(("export_err", str(exc)))
            except Exception as exc:
                events.put(("export_err", f"Unexpected error: {exc!r}"))

        threading.Thread(target=worker, daemon=True).start()

    def finish_export(export):
        state["busy_export"] = False
        btn_export.configure(state="normal")
        n = len(export["skins"])
        safe = re.sub(r"[^\w#-]+", "_", export["player"]).strip("_")
        path = filedialog.asksaveasfilename(
            parent=root, title="Save your skin library",
            defaultextension=".json",
            initialfile=f"{safe or 'my'}_skins.json",
            filetypes=[("Skin library JSON", "*.json")])
        if path:
            Path(path).write_text(json.dumps(export, indent=2),
                                  encoding="utf-8")
            set_status(f"Exported {n} skins for {export['player']} to "
                       f"{path} — send that file to whoever is comparing!")
        try:
            add_library_from_data(export)
        except ValueError as exc:
            messagebox.showwarning(APP_NAME, str(exc), parent=root)

    def export_failed(msg):
        state["busy_export"] = False
        btn_export.configure(state="normal")
        set_status("Export failed.")
        if "No running League client" in msg:
            if messagebox.askyesno(
                    APP_NAME,
                    msg + "\n\nDo you want to locate the lockfile manually "
                          "now?", parent=root):
                lock = filedialog.askopenfilename(
                    parent=root, title="Locate your League 'lockfile'",
                    filetypes=[("lockfile", "lockfile"),
                               ("All files", "*.*")])
                if lock:
                    state["busy_export"] = True
                    btn_export.configure(state="disabled")
                    set_status("Trying the lockfile you picked...")

                    def worker():
                        try:
                            export = export_my_skins(state["gamedata"],
                                                     extra_lockfile=lock)
                            events.put(("export_ok", export))
                        except Exception as exc:
                            events.put(("export_err", str(exc)))

                    threading.Thread(target=worker, daemon=True).start()
        else:
            messagebox.showerror(f"{APP_NAME} — export failed", msg,
                                 parent=root)

    btn_export.configure(command=do_export)
    btn_add.configure(command=add_files)
    btn_merge.configure(command=merge_accounts)
    btn_export.configure(state="disabled")  # until game data arrives

    only_full_var.trace_add("write", refresh_table)
    merge_var.trace_add("write", refresh_table)
    search_var.trace_add("write", refresh_table)
    mastery_on_var.trace_add("write", refresh_table)
    mastery_n_var.trace_add("write", lambda *_: refresh_table())

    def on_lock_change(*_):
        talon_var.set(team_lock_var.get().strip().lower() == "talon")
        refresh_team()

    team_lock_var.trace_add("write", on_lock_change)

    # ---- background game data load --------------------------------------
    def data_worker():
        try:
            gd = load_game_data(lambda m: events.put(("status", m)))
            events.put(("gamedata", gd))
        except Exception as exc:
            events.put(("data_err", str(exc)))

    threading.Thread(target=data_worker, daemon=True).start()

    def poll_events():
        try:
            while True:
                kind, payload = events.get_nowait()
                if kind == "status":
                    set_status(payload)
                elif kind == "gamedata":
                    state["gamedata"] = payload
                    btn_export.configure(state="normal")
                    # re-resolve any libraries loaded before data arrived
                    if state["raw"]:
                        order = [l.player for l in state["libraries"]]
                        state["libraries"] = [
                            parse_library(state["raw"][name], payload,
                                          display_name=name)
                            for name in order if name in state["raw"]
                        ]
                        refresh_players()
                        refresh_table()
                    set_status(
                        f"Ready — {len(payload.skinline_names)} skinlines "
                        "known. Export your skins or add friends' files.")
                elif kind == "data_err":
                    set_status("Couldn't load skinline data.")
                    messagebox.showerror(APP_NAME, payload, parent=root)
                elif kind == "export_ok":
                    finish_export(payload)
                elif kind == "export_err":
                    export_failed(payload)
        except queue.Empty:
            pass
        root.after(100, poll_events)

    refresh_players()
    refresh_table()
    load_roster()

    if preload:
        errors = []
        for path in preload:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                add_library_from_data(data)
            except Exception as exc:
                errors.append(f"{Path(path).name}: {exc}")
        if errors:
            set_status("Couldn't load: " + "; ".join(errors))

    # ---- welcome screen ---------------------------------------------------
    def show_welcome():
        win = tk.Toplevel(root)
        win.title(f"Welcome — {APP_NAME}")
        win.transient(root)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=(20, 16))
        frame.pack(fill="both", expand=True)
        try:
            icon = tk.PhotoImage(data=APP_ICON_PNG_B64)
            state["welcome_icon"] = icon  # keep a reference alive
            ttk.Label(frame, image=icon).grid(row=0, column=0, rowspan=2,
                                              padx=(0, 14))
        except Exception:
            pass
        ttk.Label(frame, text=APP_NAME,
                  font=("Segoe UI", 16, "bold")).grid(row=0, column=1,
                                                      sticky="sw")
        ttk.Label(frame, text=f"v{APP_VERSION}  ·  made by {APP_AUTHOR}",
                  foreground="#777").grid(row=1, column=1, sticky="nw")
        guide = (
            "1.  Open League, log in, hit \"Export My Skins...\" — "
            "save your file.\n"
            "2.  Swap files with your friends, add theirs with "
            "\"Add Friend's Library...\".\n"
            "3.  Green rows = skinlines you can ALL play. Click one for "
            "the lineup.\n"
            "4.  Got 4-5 players? The Team Builder tab splits you into "
            "roles too.\n\n"
            "Everything stays on your PC — nothing is ever uploaded."
        )
        ttk.Label(frame, text=guide, justify="left",
                  font=("Segoe UI", 10)).grid(row=2, column=0,
                                              columnspan=2, sticky="w",
                                              pady=(14, 10))
        show_var = tk.BooleanVar(value=state.get("show_welcome", True))
        ttk.Checkbutton(frame, text="Show this when the app starts",
                        variable=show_var).grid(row=3, column=0,
                                                columnspan=2, sticky="w")

        def close_welcome():
            state["show_welcome"] = bool(show_var.get())
            write_roster()
            win.destroy()

        ttk.Button(frame, text="Let's go!", command=close_welcome
                   ).grid(row=4, column=0, columnspan=2, pady=(12, 0))
        win.protocol("WM_DELETE_WINDOW", close_welcome)
        win.update_idletasks()
        x = root.winfo_rootx() + max(
            0, (root.winfo_width() - win.winfo_reqwidth()) // 2)
        y = root.winfo_rooty() + max(
            0, (root.winfo_height() - win.winfo_reqheight()) // 3)
        win.geometry(f"+{x}+{y}")
        win.lift()
        win.focus_set()

    if state.get("show_welcome", True):
        root.after(250, show_welcome)

    root.after(100, poll_events)
    root.mainloop()


# --------------------------------------------------------------------------
# Self test (no GUI)
# --------------------------------------------------------------------------

def selftest(online=True):
    failures = []

    def check(label, cond):
        print(("  OK  " if cond else " FAIL ") + label)
        if not cond:
            failures.append(label)

    print("== matching ==")
    a = find_assignment({"A": {"Lux"}, "B": {"Lux", "Jinx"}})
    check("2-player solvable", a == {"A": "Lux", "B": "Jinx"})
    check("2-player clash", find_assignment({"A": {"Lux"},
                                             "B": {"Lux"}}) is None)
    a = find_assignment({"A": {"Lux", "Jinx"}, "B": {"Lux"},
                         "C": {"Jinx", "Sett"}})
    check("3-player needs augmenting path",
          a is not None and len(set(a.values())) == 3)

    print("== normalization ==")
    check("season merge",
          normalize_skinline("Star Guardian Season 3") == "Star Guardian")
    check("no false merge",
          normalize_skinline("World Champions: 2014")
          == "World Champions: 2014")

    print("== export/parse round trip ==")
    gd = GameData(
        skinline_names={7: "High Noon", 8: "Pool Party"},
        champ_names={236: "Lucian", 21: "Miss Fortune"},
        skins={236009: ("High Noon Lucian", [7]),
               21015: ("Pool Party Miss Fortune", [8])},
    )
    raw = [
        {"id": 236009, "name": "High Noon Lucian", "isBase": False,
         "ownership": {"owned": True}},
        {"id": 236000, "name": "Lucian", "isBase": True,
         "ownership": {"owned": True}},
        {"id": 21015, "name": "Pool Party Miss Fortune", "isBase": False,
         "ownership": {"owned": False}},
    ]
    export = build_export("Tester#NA1", raw, gd)
    check("base + unowned filtered", len(export["skins"]) == 1)
    check("skinline resolved",
          export["skins"][0]["skinlines"] == ["High Noon"])
    lib = parse_library(json.loads(json.dumps(export)), gd)
    check("round trip player", lib.player == "Tester#NA1")
    check("round trip champion", lib.records[0]["champion"] == "Lucian")

    lib2 = Library("Friend#EUW", [
        {"id": 21015, "name": "Pool Party Miss Fortune",
         "champion": "Miss Fortune", "skinlines": ["High Noon"]},
    ])
    rows = build_rows([lib, lib2])
    hn = next(r for r in rows if r["line"] == "High Noon")
    check("full match found across players",
          hn["full"] and hn["assignment"] is not None)

    print("== unknown skin fallback ==")
    lib3 = parse_library({"player": "Old#File", "skins": [
        {"id": 99999999, "name": "Mystery Skin", "champion": "MysteryChamp",
         "skinlines": ["Mystery Line"]}]}, gd)
    check("fallback to embedded names",
          lib3.records[0]["skinlines"] == ["Mystery Line"]
          and lib3.records[0]["champion"] == "MysteryChamp")

    print("== champion filter ==")
    rows = build_rows([lib, lib2])
    hn_row = next(r for r in rows if r["line"] == "High Noon")
    check("filter hits skinline name", row_matches(hn_row, "high noo"))
    check("filter hits champion name", row_matches(hn_row, "lucian"))
    check("filter misses others", not row_matches(hn_row, "teemo"))
    check("empty filter matches", row_matches(hn_row, ""))

    print("== mastery ==")
    mlib = parse_library({"player": "M#NA1", "skins": [
        {"id": 236009}, {"id": 21015}],
        "mastery": {"236": 50000, "21": 250000}}, gd)
    check("mastery parsed to champ names",
          mlib.mastery == {"Lucian": 50000, "Miss Fortune": 250000})
    check("top-1 mastery keeps MF only",
          top_mastery_champs(mlib, 1) == {"Miss Fortune"})
    check("no mastery data = unrestricted",
          top_mastery_champs(lib3, 1) is None)
    matrix = build_matrix([mlib], top_mastery=1)
    check("matrix respects mastery filter",
          "High Noon" not in matrix and "Pool Party" in matrix)
    # two perfect matchings exist; mastery should steer A to their main
    a = find_assignment(
        {"A": {"Lux", "Jinx"}, "B": {"Jinx", "Sett"}},
        {"A": {"Lux": 100000, "Jinx": 5}, "B": {}})
    check("assignment prefers high mastery",
          a is not None and a["A"] == "Lux")

    print("== skinline styles ==")
    check("Pool Party is turquoise beach",
          style_for("Pool Party") == ("🏖", "#1fc3c3"))
    check("High Noon is orange cowboy",
          style_for("High Noon") == ("🤠", "#e07b1f"))
    check("season variants share a style",
          style_for("Star Guardian Season 3") == style_for("Star Guardian"))
    check("keyword rule catches new lines",
          style_for("Lunar Revel: Firecracker") == ("🧧", "#c0392b"))
    check("unknown line gets default",
          style_for("Some Future 2027 Line") == DEFAULT_STYLE)

    print("== team maker ==")
    roles = {"Lux": {"Mid", "Support"}, "Jinx": {"Bot"}, "Sett": {"Top"},
             "Vi": {"Jungle"}, "Thresh": {"Support"}, "Draven": {"Bot"}}
    comp = find_team_comp(
        {"A": {"Lux"}, "B": {"Jinx"}, "C": {"Sett"}, "D": {"Vi"},
         "E": {"Thresh"}}, roles)
    check("5 players fill all five roles",
          comp is not None
          and sorted(r for _, r in comp.values()) == sorted(ROLES))
    check("Lux pushed to Mid when Thresh needs Support",
          comp is not None and comp["A"] == ("Lux", "Mid"))
    comp4 = find_team_comp(
        {"A": {"Lux"}, "B": {"Jinx"}, "C": {"Sett"}, "D": {"Vi"}}, roles)
    check("4 players leave exactly one role empty",
          comp4 is not None and len(comp4) == 4
          and len({r for _, r in comp4.values()}) == 4)
    check("two Bot-only champs can't both fit",
          find_team_comp({"A": {"Jinx"}, "B": {"Draven"}, "C": {"Sett"},
                          "D": {"Vi"}}, roles) is None)
    check("champ without role data is flexible",
          find_team_comp({"A": {"MysteryChamp"}, "B": {"Jinx"},
                          "C": {"Sett"}, "D": {"Vi"}}, roles) is not None)
    tlib1 = Library("P1", [{"id": 1, "name": "s", "champion": "Lux",
                            "skinlines": ["High Noon"]}])
    tlib2 = Library("P2", [{"id": 2, "name": "s", "champion": "Jinx",
                            "skinlines": ["High Noon"]}])
    trows = build_team_rows([tlib1, tlib2], roles)
    check("team rows only list lines everyone owns",
          len(trows) == 1 and trows[0]["line"] == "High Noon"
          and trows[0]["comp"] is not None)
    check("role pools list who fits where",
          trows[0]["role_pools"]["P1"]["Mid"] == ["Lux"]
          and trows[0]["role_pools"]["P1"]["Top"] == []
          and trows[0]["role_pools"]["P2"]["Bot"] == ["Jinx"])

    print("== account merging ==")
    main_acc = Library("Main#1", [
        {"id": 1, "name": "s1", "champion": "Lux", "skinlines": ["X"]},
        {"id": 2, "name": "s2", "champion": "Jinx", "skinlines": ["X"]}],
        {"Lux": 100, "Jinx": 5})
    smurf = Library("Smurf#2", [
        {"id": 2, "name": "s2", "champion": "Jinx", "skinlines": ["X"]},
        {"id": 3, "name": "s3", "champion": "Sett", "skinlines": ["Y"]}],
        {"Jinx": 50})
    merged = merge_libraries("Me (both)", [main_acc, smurf])
    check("merged pools and dedupes skins", len(merged.records) == 3)
    check("merged mastery takes the max",
          merged.mastery == {"Lux": 100, "Jinx": 50})
    check("merged remembers its members",
          merged.merged_members == ["Main#1", "Smurf#2"])
    mm = build_matrix([merged])
    check("merged plays as one player",
          mm["X"] == {"Me (both)": {"Lux", "Jinx"}})

    print("== my-account mastery scope ==")
    # mlib's top-1 is Miss Fortune, so top_mastery=1 drops their High Noon
    # Lucian. flib's top-1 is Sona, which would drop their High Noon too —
    # unless the filter is scoped to mlib only.
    flib = Library("F#EUW", [
        {"id": 236009, "name": "High Noon Lucian", "champion": "Lucian",
         "skinlines": ["High Noon"]}],
        {"Lucian": 10, "Sona": 999})
    scoped = build_matrix([mlib, flib], top_mastery=1,
                          mastery_scope="M#NA1")
    check("scoped: my account filtered, friend untouched",
          "High Noon" in scoped
          and set(scoped["High Noon"]) == {"F#EUW"})
    unscoped = build_matrix([mlib, flib], top_mastery=1)
    check("unscoped: filter hits everyone", "High Noon" not in unscoped)

    if online:
        print("== live CommunityDragon fetch ==")
        gd_live = load_game_data(lambda m: print("      " + m),
                                 force_refresh=True)
        names = set(gd_live.skinline_names.values())
        check("High Noon known", "High Noon" in names)
        check("Pool Party known", "Pool Party" in names)
        check("champions loaded", "Annie" in gd_live.champ_names.values())
        check("skins loaded (>1500)", len(gd_live.skins) > 1500)
        check("Goth Annie -> a skinline",
              len(gd_live.skinlines_for(1001)) >= 1)
        check("champion positions loaded",
              "Mid" in gd_live.champ_positions.get("Annie", set()))
        n_pos = len(gd_live.champ_positions)
        check("positions cover most champions (>120)", n_pos > 120)
        check("Rek'Sai plays everywhere",
              set(gd_live.champ_positions.get("Rek'Sai", [])) == set(ROLES))
        yone = set(gd_live.champ_positions.get("Yone", []))
        check("Yone flexes top/mid/bot",
              {"Top", "Mid", "Bot"} <= yone)
        print("== LCU detection (client may not be running) ==")
        lcu = find_lcu()
        print(f"      League client {'FOUND on port ' + str(lcu.port) if lcu else 'not running — skipping live export test'}")
        if lcu:
            export = export_my_skins(gd_live)
            check("live export produced skins",
                  isinstance(export["skins"], list))
            print(f"      exported {len(export['skins'])} owned skins "
                  f"for {export['player']}")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("All self tests passed.")
    return 0


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--export", nargs="?", const="", metavar="OUTFILE",
                        help="export owned skins without opening the GUI")
    parser.add_argument("--load", nargs="+", metavar="FILE",
                        help="open the GUI with these library files "
                             "already loaded")
    parser.add_argument("--data-file", metavar="FILE",
                        help="where to remember added friends "
                             f"(default: {DATA_FILE})")
    parser.add_argument("--selftest", action="store_true",
                        help="run built-in tests and exit")
    parser.add_argument("--offline", action="store_true",
                        help="with --selftest: skip network tests")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest(online=not args.offline)

    if args.export is not None:
        gd = load_game_data(lambda m: print(m))
        try:
            export = export_my_skins(gd)
        except ExportError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        safe = re.sub(r"[^\w#-]+", "_", export["player"]).strip("_")
        out = Path(args.export) if args.export else \
            Path(f"{safe or 'my'}_skins.json")
        out.write_text(json.dumps(export, indent=2), encoding="utf-8")
        print(f"Exported {len(export['skins'])} owned skins for "
              f"{export['player']} -> {out}")
        return 0

    run_gui(preload=args.load, data_file=args.data_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
