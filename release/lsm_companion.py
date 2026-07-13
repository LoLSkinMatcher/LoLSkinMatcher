#!/usr/bin/env python3
"""
League Skin Matcher — Companion (v2.0 prototype)

Double-click (or run with no arguments) for the mini app: a small
window with two controls —

    [ Upload my library ]   sync your owned skins to the group database
    [ Watch live lobby  ]   captain mode: follow your lobby/champ select
                            and feed the party web page in real time

Console use still works for scripting:

    python lsm_companion.py upload   [--dry-run]
    python lsm_companion.py watch    [--dry-run]

No setup: the group's Firebase project is built in, so the exe works
on its own. (A firebase_config.json next to it overrides the defaults,
e.g. for a fork running its own project.) Uses Firebase ANONYMOUS
auth: no account, no sign-in; a silent token is created on first run
and cached in your user folder.

Standard library only, same as the main app.
"""

import argparse
import json
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import league_skin_matcher as lsm

# watch mode is often run with output redirected; keep prints live
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

AUTH_CACHE = Path.home() / ".lsm_companion_auth.json"
POLL_SECONDS = 3
# Every shared skinline is shown. STATE_BUDGET is only a safety net: the
# Firestore rules cap the party doc's `state` string (see firestore.rules),
# so if a very large group would overflow it we trim the lowest-priority
# cards rather than let the write be rejected and stall the page.
STATE_BUDGET = 190000
COMPANION_VERSION = "2.5"
APP_TITLE = f"LoLSkinMatcher Companion  v{COMPANION_VERSION}"

# Same-person alternate accounts. In the LOBBY only (never champ select), if
# one account in a group is in the party, the others' libraries are also
# considered: a skinline only the alt can field is surfaced as "needs a quick
# account switch". PUUIDs are public (they're already the party-page URL and
# Firestore keys), so nothing secret lives here. Baked in so every captain's
# companion knows the link, not just the owner's.
ALT_GROUPS = [
    ["68a5fce3-5123-56ad-ab23-88e1198dccb0",   # Mike Oxmaul#NA5
     "d3ae4acb-eeb2-5292-9ee0-328a7837d09c",   # StallionPrime#9125
     "7912f5a7-48de-56dd-98d5-6f178cb51771"],  # HyperNova3#NA1
]


def alt_puuids(puuid):
    """Other accounts in the same person's alt-group (or [] if none)."""
    for group in ALT_GROUPS:
        if puuid in group:
            return [p for p in group if p != puuid]
    return []

# The group's Firebase project, baked in so the exe works on its own.
# These values are PUBLIC by design (the web page ships them to every
# visitor); security lives in the Firestore rules. A firebase_config.json
# next to the exe/script overrides them (e.g. for a fork's own project).
DEFAULT_CONFIG = {
    "apiKey": "AIzaSyBbawpCeLpf3lvLyfxwHDEtNE3_eNAq6jA",
    "projectId": "lolskinmatcher",
    "webUrl": "https://lolskinmatcher.web.app",
}


class CompanionError(RuntimeError):
    """A problem the user can fix (no client, missing config, ...)."""


def _config_path():
    """firebase_config.json next to the script/exe, or one level up."""
    bases = []
    if getattr(sys, "frozen", False):  # PyInstaller exe
        bases.append(Path(sys.executable).resolve().parent)
    here = Path(__file__).resolve().parent
    bases += [here, here.parent]
    for base in bases:
        for extra in (base, base.parent):
            candidate = extra / "firebase_config.json"
            if candidate.is_file():
                return candidate
    return here / "firebase_config.json"


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def http_json(url, payload=None, method=None, bearer=None, timeout=30):
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    data = json.dumps(payload).encode("utf-8") if payload is not None \
        else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def s(value):  # Firestore string field
    return {"stringValue": str(value)}


def load_config():
    """Built-in group config, overridable by a firebase_config.json."""
    cfg = dict(DEFAULT_CONFIG)
    path = _config_path()
    if path.is_file():
        try:
            override = json.loads(path.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in override.items() if v})
        except Exception as exc:
            raise CompanionError(
                f"{path.name} exists but couldn't be read: {exc}")
    if not cfg.get("apiKey") or not cfg.get("projectId"):
        raise CompanionError("Config needs both apiKey and projectId.")
    return cfg


# --------------------------------------------------------------------------
# Firebase anonymous auth (Identity Toolkit REST)
# --------------------------------------------------------------------------

def sign_in(cfg):
    """Anonymous sign-in with a cached, refreshable token."""
    cached = {}
    if AUTH_CACHE.is_file():
        try:
            cached = json.loads(AUTH_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cached = {}
    if cached.get("apiKey") == cfg["apiKey"] and cached.get("refreshToken"):
        try:
            tok = http_json(
                f"https://securetoken.googleapis.com/v1/token"
                f"?key={cfg['apiKey']}",
                {"grant_type": "refresh_token",
                 "refresh_token": cached["refreshToken"]})
            cached.update(idToken=tok["id_token"],
                          refreshToken=tok["refresh_token"],
                          uid=tok["user_id"])
            AUTH_CACHE.write_text(json.dumps(cached), encoding="utf-8")
            return cached
        except Exception:
            pass  # fall through to a fresh anonymous account
    fresh = http_json(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signUp"
        f"?key={cfg['apiKey']}",
        {"returnSecureToken": True})
    cached = {"apiKey": cfg["apiKey"], "idToken": fresh["idToken"],
              "refreshToken": fresh["refreshToken"],
              "uid": fresh["localId"]}
    AUTH_CACHE.write_text(json.dumps(cached), encoding="utf-8")
    return cached


# --------------------------------------------------------------------------
# Firestore REST
# --------------------------------------------------------------------------

def fs_url(cfg, path):
    return (f"https://firestore.googleapis.com/v1/projects/"
            f"{cfg['projectId']}/databases/(default)/documents/{path}")


def fs_set(cfg, auth, path, fields):
    return http_json(fs_url(cfg, path), {"fields": fields},
                     method="PATCH", bearer=auth["idToken"])


def fs_get(cfg, auth, path):
    try:
        return http_json(fs_url(cfg, path), bearer=auth["idToken"])
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def field_str(doc, name):
    return ((doc or {}).get("fields", {}).get(name, {})
            .get("stringValue"))


def fetch_library_cached(puuid, cache, fetch):
    """Return a player's library dict, from cache or a fresh fetch.

    Only SUCCESSFUL fetches are cached — a miss is never stored — so a
    library uploaded AFTER the player joined the lobby is retried on the
    next poll instead of being stuck at "no library yet". `fetch(puuid)`
    returns the library dict, or None if not uploaded yet.
    """
    data = cache.get(puuid)
    if data is None:
        data = fetch(puuid)
        if data:
            cache[puuid] = data
    return data


# --------------------------------------------------------------------------
# LCU helpers (on top of the main app's client discovery)
# --------------------------------------------------------------------------

def require_lcu():
    lcu = lsm.find_lcu()
    if lcu is None:
        raise CompanionError("No running League client found — open League, "
                         "log in, and try again.")
    return lcu


def _full_name(summoner):
    game = summoner.get("gameName") or summoner.get("displayName") or ""
    tag = summoner.get("tagLine") or ""
    if game and tag:
        return f"{game}#{tag}"
    return game or None


def lobby_members(lcu):
    """[{puuid, name-or-None}] for the current lobby; None when not in
    a lobby. Connection failures (client closed) raise, so the watch
    loop can tell 'no lobby' apart from 'no client'."""
    try:
        lobby = lcu.get("/lol-lobby/v2/lobby")
    except urllib.error.HTTPError:
        return None  # logged in, just not in a lobby right now
    members = []
    for m in lobby.get("members", []):
        if m.get("puuid"):
            members.append({"puuid": m["puuid"], "name": _full_name(m)})
    return members or None


def champ_select(lcu):
    try:
        return lcu.get("/lol-champ-select/v1/session")
    except urllib.error.HTTPError:
        return None  # not in champ select


# Riot's champ-select position names -> our role labels.
POSITION_TO_ROLE = {"top": "Top", "jungle": "Jungle", "middle": "Mid",
                    "bottom": "Bot", "utility": "Support"}


def read_bans(cs, champ_names):
    """Banned champion names in a champ-select session.

    session.bans.myTeamBans/theirTeamBans are often empty, so also scan the
    completed 'ban' actions (the authoritative source). Deduped, in order,
    ignoring empty slots (championId 0/-1).
    """
    ban_ids = list((cs.get("bans") or {}).get("myTeamBans", [])) \
        + list((cs.get("bans") or {}).get("theirTeamBans", []))
    for group in cs.get("actions", []):
        for act in group:
            if act.get("type") == "ban" and act.get("completed"):
                ban_ids.append(act.get("championId"))
    out, seen = [], set()
    for cid in ban_ids:
        if not cid or cid <= 0 or cid in seen:
            continue
        seen.add(cid)
        name = champ_names.get(cid)
        if name:
            out.append(name)
    return out


def read_role_prefs(cs, name_by_puuid):
    """{player: [assigned role]} from champ select's myTeam.assignedPosition,
    so the suggested comp seats people in the roles they're actually taking."""
    prefs = {}
    for t in cs.get("myTeam", []):
        player = name_by_puuid.get(t.get("puuid"))
        role = POSITION_TO_ROLE.get((t.get("assignedPosition") or "").lower())
        if player and role:
            prefs[player] = [role]
    return prefs


# --------------------------------------------------------------------------
# Suggestions (reuses the main app's solver, minus banned/taken champs)
# --------------------------------------------------------------------------

def _skin_index(libraries):
    """{player: {(normalized_line, champ): skin_name}}, newest skin per
    champion per line — lets the grid show the exact skin to equip (e.g.
    'Queen of Diamonds Syndra' for a Highstakes Syndra, where the skin name
    doesn't just echo the line + champion)."""
    idx = {}
    for lib in libraries:
        per = idx.setdefault(lib.player, {})
        for rec in lib.records:
            champ, sid, name = rec["champion"], rec.get("id", 0), rec.get("name")
            for line in rec["skinlines"]:
                key = (lsm.normalize_skinline(line), champ)
                prev = per.get(key)
                if prev is None or sid > prev[0]:
                    per[key] = (sid, name)
    return {p: {k: v[1] for k, v in d.items()} for p, d in idx.items()}


def compute_suggestions(gd, libraries, blocked_names, pinned, role_prefs=None):
    """Skinline comps still possible for these players.

    blocked_names: champions removed from everyone's pools (bans + enemy
    picks). pinned: {player_name: champion} for teammates already locked
    in — lines that can't seat their lock are dropped. role_prefs:
    {player: [roles]} to seat people in their champ-select-assigned lanes.

    Only lines that yield a real comp are returned. A line is shown when
    a comp seats EVERYONE; for a full five-stack it may instead show a
    4/5 comp (one player off theme). Lines with no such comp are dropped
    rather than shown as "no full comp" — that clutter lives only in the
    grid you'd never play. (ARAM uses compute_aram, which keeps partials.)
    """
    matrix = lsm.build_matrix(libraries)
    skins = _skin_index(libraries)
    mastery = {lib.player: lib.mastery for lib in libraries}
    order = [lib.player for lib in libraries]
    total = len(order)
    out = []
    for line, per_player in matrix.items():
        if len(per_player) != len(libraries):
            continue
        pools = {}
        for player in order:
            pool = [c for c in per_player.get(player, ())
                    if c not in blocked_names]
            lock = pinned.get(player)
            if lock:
                pool = [lock] if lock in pool else []
            pools[player] = pool

        # Prefer a comp that seats everyone. If that's impossible and the
        # party is a full five-stack, fall back to a 4/5 comp (one player
        # sits out the theme). Smaller parties only ever show a complete
        # comp; anything less is dropped, not badged.
        comp = None
        if all(pools[p] for p in order):
            comp = lsm.find_team_comp(pools, gd.champ_positions, mastery,
                                      role_prefs=role_prefs)
        if comp is None and total == 5:
            for sit_out in order:
                sub = {p: pools[p] for p in order if p != sit_out}
                comp = lsm.find_team_comp(sub, gd.champ_positions, mastery,
                                          role_prefs=role_prefs)
                if comp:
                    break
        if not comp:
            continue
        seated = len(comp)
        picks = {(p, role, champ) for p, (champ, role) in comp.items()}

        # the 5x5 grid: for each player (row) and lane (column), every
        # champion they can play there, mastery-sorted, marking the
        # suggested pick (the off-theme player, if any, has no pick)
        grid = []
        for player in order:
            m = mastery.get(player, {})
            cells = {}
            for role in lsm.ROLES:
                champs = sorted(
                    (c for c in pools[player]
                     if role in (gd.champ_positions.get(c) or lsm.ROLES)),
                    key=lambda c: (-m.get(c, 0), c))
                cells[role] = [
                    {"champ": c, "champId": gd.champ_ids.get(c),
                     "skin": skins.get(player, {}).get((line, c)),
                     "pick": (player, role, c) in picks}
                    for c in champs]
            grid.append({"player": player, "cells": cells})

        emoji, color = lsm.style_for(line)
        out.append({
            "line": line, "emoji": emoji, "color": color,
            "ok": seated == total,
            "seated": seated,
            "total": total,
            "comp": [{"role": role, "player": player, "champ": champ,
                      "champId": gd.champ_ids.get(champ),
                      "skin": skins.get(player, {}).get((line, champ))}
                     for player, (champ, role) in comp.items()],
            "grid": grid,
        })
    out.sort(key=lambda r: (not r["ok"], r["line"].lower()))
    return out


def compute_suggestions_with_alts(gd, party, blocked, pinned, fetch):
    """Lobby suggestions that also consider each member's alternate accounts.

    party: [{"puuid", "name", "lib"}] for every lobby member (lib may be None).
    fetch: puuid -> library dict (cached), for pulling an alt's library.

    Returns compute_suggestions cards tagged with:
      access = "current"  -> playable on the account already in the lobby
      access = "switch"   -> only playable after that member switches to an
                             alt (switchTo = that account's name).
    A line playable now is always "current" (no switch suggested), even if an
    alt could also field it. Only lines that need the alt become "switch".
    """
    base_libs = [m["lib"] for m in party if m["lib"]]
    if len(base_libs) < 2:
        return []

    current = compute_suggestions(gd, base_libs, blocked, pinned)
    for s in current:
        s["access"] = "current"
    seen = {s["line"] for s in current}
    extra = []

    for m in party:
        if not m["lib"]:
            continue
        for ap in alt_puuids(m["puuid"]):
            data = fetch(ap)
            if not data:
                continue
            alt_name = data.get("player") or ap
            # parse under the ALT account's own name, so the grid/comp row
            # shows the account you'd switch INTO (not the one in the lobby).
            alt_lib = lsm.parse_library(data, gd, display_name=alt_name)
            alt_libs = [alt_lib if x is m["lib"] else x for x in base_libs]
            for s in compute_suggestions(gd, alt_libs, blocked, pinned):
                if s["line"] in seen:
                    continue           # already playable now, or via another alt
                s["access"] = "switch"
                s["switchTo"] = alt_name
                s["switchFrom"] = m["name"]
                seen.add(s["line"])
                extra.append(s)

    result = current + extra
    # current lines first, then switch lines; full comps before partials
    result.sort(key=lambda s: (s.get("access") == "switch",
                               not s["ok"], s["line"].lower()))
    return result


def max_assignment(player_champs):
    """Assign as many players as possible a DISTINCT champion from their
    set (max bipartite matching, Kuhn's). Returns {player: champion} —
    may cover fewer than all players. No roles (ARAM is all-mid)."""
    players = list(player_champs)
    owner = {}  # champion -> player index

    def augment(i, visited):
        for champ in sorted(player_champs[players[i]]):
            if champ in visited:
                continue
            visited.add(champ)
            if champ not in owner or augment(owner[champ], visited):
                owner[champ] = i
                return True
        return False

    for i in range(len(players)):
        augment(i, set())
    return {players[idx]: champ for champ, idx in owner.items()}


def compute_aram(gd, libraries, rolled_by_name, bench_names):
    """ARAM skinline roulette: for each skinline, how many party members
    can end up on a DISTINCT champion in that line — playing their rolled
    champ or swapping to a shared bench champ — that they own a skin for.

    rolled_by_name: {player: champion currently rolled to them}.
    bench_names: champions on the shared bench (anyone can grab, once).
    Returns matchable lines (>=2 players), full-party matches first.
    """
    matrix = lsm.build_matrix(libraries)   # {line: {player: owned champs}}
    skins = _skin_index(libraries)
    party = [lib.player for lib in libraries]
    bench = set(bench_names)
    out = []
    for line, per_player in matrix.items():
        cand = {}
        for p in party:
            owned = per_player.get(p, set())
            avail = set(bench)
            rolled = rolled_by_name.get(p)
            if rolled:
                avail.add(rolled)
            hit = owned & avail      # in this line, owns a skin, reachable
            if hit:
                cand[p] = hit
        if len(cand) < 2:
            continue
        assign = max_assignment(cand)
        if len(assign) < 2:
            continue
        emoji, color = lsm.style_for(line)
        out.append({
            "line": line, "emoji": emoji, "color": color,
            "count": len(assign), "total": len(party),
            "full": len(assign) == len(party),
            "assignment": [
                {"player": p, "champ": c, "champId": gd.champ_ids.get(c),
                 "skin": skins.get(p, {}).get((line, c)),
                 "source": "rolled" if rolled_by_name.get(p) == c
                 else "bench"}
                for p, c in sorted(assign.items(),
                                   key=lambda kv: kv[0])],
        })
    out.sort(key=lambda r: (not r["full"], -r["count"], r["line"].lower()))
    return out


def fit_state(state, budget=STATE_BUDGET):
    """Keep the pushed party state under the Firestore rules' size cap.

    Cards are already sorted best-first (full comps / fuller ARAM matches
    first), so if an unusually large shared library would overflow the doc
    we drop the lowest-priority cards until it fits, rather than letting the
    write get rejected and freeze the page. Normal friend-group parties are
    well under budget and untouched.
    """
    key = "aram" if state.get("aramMode") else "suggestions"
    while state.get(key) and len(json.dumps(state, sort_keys=True)) > budget:
        state[key] = state[key][:-1]
    return state


# --------------------------------------------------------------------------
# Core actions (shared by GUI and CLI)
# --------------------------------------------------------------------------

def do_upload(log=print, dry_run=False):
    """Export from the client and upload to Firestore. Returns (player,
    skin count)."""
    lcu = require_lcu()
    summoner = lcu.get("/lol-summoner/v1/current-summoner")
    puuid = summoner.get("puuid")
    if not puuid:
        raise CompanionError("Couldn't read your PUUID from the client.")
    gd = lsm.load_game_data(log)
    export = lsm.export_my_skins(gd)
    blob = json.dumps(export)
    log(f"{export['player']}: {len(export['skins'])} skins, "
        f"{len(blob) / 1024:.0f} KB")
    if dry_run:
        log(f"[dry-run] would write libraries/{puuid}")
        return export["player"], len(export["skins"])
    cfg = load_config()
    auth = sign_in(cfg)
    fs_set(cfg, auth, f"libraries/{puuid}", {
        "player": s(export["player"]),
        "puuid": s(puuid),
        "updatedAt": s(now_iso()),
        "data": s(blob),
    })
    log("Uploaded! Re-run after you buy new skins.")
    return export["player"], len(export["skins"])


def watch_loop(log=print, stop=None, dry_run=False, on_link=None):
    """Captain mode. Runs until `stop` (threading.Event) is set.

    Survives the client closing: publishes an "offline" state and
    reconnects automatically when League comes back.
    """
    stop = stop or threading.Event()
    cfg = auth = None
    if not dry_run:
        cfg = load_config()
        auth = sign_in(cfg)
    lcu = require_lcu()
    summoner = lcu.get("/lol-summoner/v1/current-summoner")
    captain = summoner.get("puuid")
    self_name = _full_name(summoner) or "You"
    gd = lsm.load_game_data(log)
    party_path = f"parties/{captain}"
    if not dry_run and cfg.get("webUrl"):
        link = f"{cfg['webUrl']}/?party={captain}"
        log(f"Party page: {link}")
        if on_link:
            on_link(link)

    libraries_cache = {}
    name_cache = {captain: self_name}
    last_pushed = None
    offline = False
    log("Watching your lobby...")

    def push(state):
        nonlocal last_pushed
        snapshot = json.dumps(state, sort_keys=True)
        if snapshot == last_pushed:
            return
        if dry_run:
            log(f"[dry-run] {state['phase']}: "
                f"{len(state['members'])} member(s), "
                f"{len(state['suggestions'])} suggestion(s), "
                f"missing: {state['missing'] or 'none'}")
        else:
            fs_set(cfg, auth, party_path, {
                "state": s(snapshot),
                "captain": s(captain),
                "updatedAt": s(now_iso()),
            })
            log(f"pushed: {state['phase']}, "
                f"{len(state['members'])} member(s), "
                f"{len(state['suggestions'])} playable line(s)")
        last_pushed = snapshot

    def resolve_name(puuid, provided):
        """Lobby name, else summoner lookup by PUUID, else the name in
        their uploaded library (fixes 'Unknown' in Practice Tool)."""
        if provided:
            name_cache[puuid] = provided
            return provided
        if puuid in name_cache:
            return name_cache[puuid]
        name = None
        try:
            summ = lcu.get(f"/lol-summoner/v2/summoners/puuid/{puuid}")
            name = _full_name(summ)
        except Exception:
            name = None
        if not name:
            data = libraries_cache.get(puuid)
            if data:
                name = data.get("player")
        name_cache[puuid] = name or "Unknown"
        return name_cache[puuid]

    OFFLINE_STATE = {"phase": "offline", "members": [], "missing": [],
                     "bans": [], "enemyPicks": [], "pinned": {},
                     "suggestions": []}

    while not stop.is_set():
        try:
            if lcu is None:
                lcu = lsm.find_lcu()
                if lcu is None or not lcu.is_alive():
                    lcu = None
                    if not offline:
                        offline = True
                        log("League client closed — waiting for it to "
                            "come back...")
                        push(dict(OFFLINE_STATE))
                    stop.wait(POLL_SECONDS)
                    continue
                offline = False
                log("League client reconnected.")

            raw = lobby_members(lcu) or [{"puuid": captain,
                                          "name": self_name}]

            def fetch_lib(puuid):
                if dry_run:
                    return None
                doc = fs_get(cfg, auth, f"libraries/{puuid}")
                blob = field_str(doc, "data")
                return json.loads(blob) if blob else None

            libs, missing, members, party = [], [], [], []
            for m in raw:
                puuid = m["puuid"]
                data = fetch_library_cached(puuid, libraries_cache,
                                            fetch_lib)
                name = resolve_name(puuid, m.get("name"))
                members.append({"puuid": puuid, "name": name})
                lib = None
                if data:
                    lib = lsm.parse_library(data, gd, display_name=name)
                    libs.append(lib)
                else:
                    missing.append(name)
                party.append({"puuid": puuid, "name": name, "lib": lib})

            cs = champ_select(lcu)
            phase = "champ select" if cs else "lobby"
            aram = cs.get("benchEnabled") if cs else False
            blocked, enemy_picks, pinned = set(), [], {}
            bans = []
            role_prefs = {}
            aram_results = []
            name_by_puuid = {m["puuid"]: m["name"] for m in members}
            if cs and aram:
                # ARAM roulette: rolled champ per party member + shared
                # bench; no bans/roles.
                rolled_by_name = {}
                for t in cs.get("myTeam", []):
                    champ = gd.champ_names.get(t.get("championId") or 0)
                    player = name_by_puuid.get(t.get("puuid"))
                    if champ and player:
                        rolled_by_name[player] = champ
                bench_names = [gd.champ_names.get(b.get("championId"))
                               for b in cs.get("benchChampions", [])]
                bench_names = [b for b in bench_names if b]
                if len(libs) >= 2:
                    aram_results = compute_aram(gd, libs, rolled_by_name,
                                                bench_names)
            elif cs:
                bans = read_bans(cs, gd.champ_names)
                role_prefs = read_role_prefs(cs, name_by_puuid)
                enemy_ids = [t.get("championId")
                             for t in cs.get("theirTeam", [])
                             if t.get("championId")]
                enemy_picks = [gd.champ_names.get(cid)
                               for cid in enemy_ids
                               if gd.champ_names.get(cid)]
                blocked = set(bans) | set(enemy_picks)
                for t in cs.get("myTeam", []):
                    champ = gd.champ_names.get(t.get("championId") or 0)
                    player = name_by_puuid.get(t.get("puuid"))
                    if champ and player:
                        pinned[player] = champ

            if aram or len(libs) < 2:
                suggestions = []
            elif cs:
                # champ select: locked to the account in the client, and
                # seated in the roles people are actually taking (role_prefs).
                # NB: don't bind a local named `s` here — it would shadow the
                # module-level s() Firestore helper that the push() closure
                # relies on, breaking every push in lobby phase.
                suggestions = compute_suggestions(gd, libs, blocked, pinned,
                                                  role_prefs=role_prefs)
                for sug in suggestions:
                    sug["access"] = "current"
            else:
                # lobby: also offer alt-account skinlines (a quick switch)
                def alt_fetch(p):
                    return fetch_library_cached(p, libraries_cache, fetch_lib)
                suggestions = compute_suggestions_with_alts(
                    gd, party, blocked, pinned, alt_fetch)

            push(fit_state({
                "phase": phase,
                "aramMode": bool(aram),
                "companionVersion": COMPANION_VERSION,
                "members": members,
                "missing": missing,
                "bans": [{"champ": b, "champId": gd.champ_ids.get(b)}
                         for b in bans],
                "enemyPicks": [{"champ": e,
                                "champId": gd.champ_ids.get(e)}
                               for e in enemy_picks],
                "pinned": pinned,
                "suggestions": suggestions,
                "aram": aram_results,
            }))
        except urllib.error.URLError:
            # client (or network) dropped mid-tick; reconnect next tick
            lcu = None
        except Exception as exc:
            log(f"(retrying) {exc}")
        stop.wait(POLL_SECONDS)
    log("Stopped watching.")


# --------------------------------------------------------------------------
# Mini GUI (default when double-clicked)
# --------------------------------------------------------------------------

def run_gui():
    import tkinter as tk
    from tkinter import ttk

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    root.title(APP_TITLE)
    root.resizable(False, False)
    try:
        root.iconphoto(True, tk.PhotoImage(data=lsm.APP_ICON_PNG_B64))
    except Exception:
        pass

    events = queue.Queue()
    state = {"busy": False, "stop": None, "link": None}

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)

    header = ttk.Frame(frame)
    header.pack(anchor="w", fill="x")
    ttk.Label(header, text="LoLSkinMatcher Companion",
              font=("Segoe UI Semibold", 14)).pack(side="left")
    ttk.Label(header, text=f"v{COMPANION_VERSION}", foreground="#888",
              font=("Segoe UI", 10)).pack(side="left", padx=(6, 0),
                                          pady=(6, 0))
    ttk.Label(frame, text="Keep it simple: upload once, watch when "
                          "you're the captain.",
              foreground="#666").pack(anchor="w", pady=(0, 12))

    btn_upload = ttk.Button(frame, text="⬆  Upload my library")
    btn_upload.pack(fill="x", pady=(0, 6))

    watch_var = tk.BooleanVar(value=False)
    chk_watch = ttk.Checkbutton(
        frame, text="👁  Watch live lobby (captain mode)",
        variable=watch_var)
    chk_watch.pack(anchor="w", pady=(0, 10))

    link_row = ttk.Frame(frame)
    link_row.pack(fill="x", pady=(0, 8))
    link_var = tk.StringVar(value="Party link appears when watching…")
    ttk.Entry(link_row, textvariable=link_var, state="readonly",
              width=46).pack(side="left", fill="x", expand=True)

    def copy_link():
        if state["link"]:
            root.clipboard_clear()
            root.clipboard_append(state["link"])
            log_line("Link copied — paste it in the group chat.")

    def open_link():
        if state["link"]:
            webbrowser.open(state["link"])

    ttk.Button(link_row, text="Copy", width=6, command=copy_link
               ).pack(side="left", padx=(6, 0))
    ttk.Button(link_row, text="Open", width=6, command=open_link
               ).pack(side="left", padx=(4, 0))

    log_box = tk.Text(frame, height=9, width=58, state="disabled",
                      relief="flat", background="#f7f7f7",
                      font=("Consolas", 9))
    log_box.pack(fill="both", expand=True)

    def log_line(msg):
        log_box.configure(state="normal")
        log_box.insert("end", f"{msg}\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    def gui_log(msg):  # thread-safe
        events.put(("log", str(msg)))

    # ---- upload -----------------------------------------------------
    def start_upload():
        if state["busy"]:
            return
        state["busy"] = True
        btn_upload.configure(state="disabled", text="Uploading…")

        def worker():
            try:
                player, n = do_upload(log=gui_log)
                events.put(("upload_done", f"{player} — {n} skins"))
            except CompanionError as exc:
                events.put(("upload_err", str(exc)))
            except Exception as exc:
                events.put(("upload_err", f"Unexpected error: {exc!r}"))

        threading.Thread(target=worker, daemon=True).start()

    btn_upload.configure(command=start_upload)

    # ---- watch toggle -------------------------------------------------
    def toggle_watch():
        if watch_var.get():
            stop = threading.Event()
            state["stop"] = stop

            def worker():
                try:
                    watch_loop(log=gui_log, stop=stop,
                               on_link=lambda url:
                               events.put(("link", url)))
                except CompanionError as exc:
                    events.put(("watch_err", str(exc)))
                except Exception as exc:
                    events.put(("watch_err",
                                f"Unexpected error: {exc!r}"))

            threading.Thread(target=worker, daemon=True).start()
        else:
            if state["stop"]:
                state["stop"].set()
                state["stop"] = None

    chk_watch.configure(command=toggle_watch)

    def poll():
        try:
            while True:
                kind, payload = events.get_nowait()
                if kind == "log":
                    log_line(payload)
                elif kind == "upload_done":
                    state["busy"] = False
                    btn_upload.configure(state="normal",
                                         text="⬆  Upload my library")
                    log_line(f"Done: {payload}")
                elif kind == "upload_err":
                    state["busy"] = False
                    btn_upload.configure(state="normal",
                                         text="⬆  Upload my library")
                    log_line(f"Upload failed: {payload}")
                elif kind == "link":
                    state["link"] = payload
                    link_var.set(payload)
                elif kind == "watch_err":
                    log_line(f"Watch failed: {payload}")
                    watch_var.set(False)
        except queue.Empty:
            pass
        root.after(150, poll)

    def on_close():
        if state["stop"]:
            state["stop"].set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    log_line("Ready. League client must be open for either action.")
    root.after(150, poll)
    root.mainloop()


# --------------------------------------------------------------------------

def selftest():
    """Regression tests for the bits that don't need League/Firebase."""
    failures = []

    def check(label, cond):
        print(("  OK  " if cond else " FAIL ") + label)
        if not cond:
            failures.append(label)

    # a library uploaded AFTER a player joins the lobby must be picked up
    db = {}                       # simulated Firestore libraries
    calls = {"n": 0}

    def fetch(puuid):
        calls["n"] += 1
        return db.get(puuid)

    cache = {}
    check("missing library -> None, not cached",
          fetch_library_cached("p1", cache, fetch) is None
          and "p1" not in cache)
    db["p1"] = {"player": "P1", "skins": []}   # player uploads now
    check("late upload picked up on next poll",
          fetch_library_cached("p1", cache, fetch) == db["p1"])
    check("now cached", cache.get("p1") == db["p1"])
    before = calls["n"]
    fetch_library_cached("p1", cache, fetch)
    check("served from cache, no re-fetch", calls["n"] == before)

    # ARAM matching: max distinct-champion assignment (no roles)
    check("max_assignment covers all when possible",
          len(max_assignment({"A": {"Lux"}, "B": {"Jinx"}})) == 2)
    check("max_assignment is partial when champs collide",
          len(max_assignment({"A": {"Lux"}, "B": {"Lux"}})) == 1)
    check("max_assignment finds augmenting path",
          len(max_assignment({"A": {"Lux", "Jinx"}, "B": {"Lux"},
                              "C": {"Jinx", "Sett"}})) == 3)

    # compute_aram: rolled + bench, owned-skin, distinct
    gd = lsm.GameData({1: "Star Guardian"},
                      {99: "Lux", 222: "Jinx", 875: "Sett", 86: "Garen"},
                      {})
    la = lsm.Library("A", [{"id": 1, "name": "s", "champion": "Lux",
                            "skinlines": ["Star Guardian"]}])
    lb = lsm.Library("B", [{"id": 2, "name": "s", "champion": "Jinx",
                            "skinlines": ["Star Guardian"]}])
    # A rolled Lux; B rolled Sett (no SG skin) but bench has Jinx (B owns)
    res = compute_aram(gd, [la, lb], {"A": "Lux", "B": "Sett"},
                       bench_names=["Jinx"])
    sg = [r for r in res if r["line"] == "Star Guardian"]
    check("ARAM full match via rolled + bench",
          bool(sg) and sg[0]["full"]
          and len(sg[0]["assignment"]) == 2)
    # neither can reach a Star Guardian champ -> no match
    res2 = compute_aram(gd, [la, lb], {"A": "Sett", "B": "Sett"},
                        bench_names=["Garen"])
    check("ARAM no match when pool has no owned line champ",
          not any(r["line"] == "Star Guardian" for r in res2))

    # every shared skinline is shown (no arbitrary count cap) -- this is the
    # bug where Pool Party went missing because only 12 lines were returned
    many = {i: f"Line {i:02d}" for i in range(1, 21)}   # 20 skinlines
    gd_many = lsm.GameData(many, {99: "Lux", 222: "Jinx"},
                           {"Lux": {"Mid"}, "Jinx": {"Bot"}})
    la2 = lsm.Library("A", [{"id": 99000 + i, "name": "s",
                             "champion": "Lux", "skinlines": [many[i]]}
                            for i in range(1, 21)])
    lb2 = lsm.Library("B", [{"id": 222000 + i, "name": "s",
                             "champion": "Jinx", "skinlines": [many[i]]}
                            for i in range(1, 21)])
    sug_all = compute_suggestions(gd_many, [la2, lb2], set(), {})
    check("every shared skinline is shown (no 12-line cap)",
          len(sug_all) == 20)

    # a line with no full comp is DROPPED (not shown as "no full comp"):
    # two players who both only own the same single champion can't field a
    # distinct duo.
    gd_solo = lsm.GameData({1: "Solo Line"}, {99: "Lux"}, {"Lux": {"Mid"}})
    lx = lsm.Library("A", [{"id": 1, "name": "s", "champion": "Lux",
                            "skinlines": ["Solo Line"]}])
    ly = lsm.Library("B", [{"id": 2, "name": "s", "champion": "Lux",
                            "skinlines": ["Solo Line"]}])
    check("no-full-comp line is hidden (2 players, same champ)",
          not any(r["line"] == "Solo Line"
                  for r in compute_suggestions(gd_solo, [lx, ly], set(), {})))

    # a full five-stack that can't seat everyone falls back to a 4/5 comp;
    # the same line for a 4-stack is a complete 4/4 comp (ok).
    gd5 = lsm.GameData({1: "Squad Line"},
                       {266: "Aatrox", 60: "Elise", 103: "Ahri", 22: "Ashe"},
                       {"Aatrox": {"Top"}, "Elise": {"Jungle"},
                        "Ahri": {"Mid"}, "Ashe": {"Bot"}})

    def one(name, champ, cid):
        return lsm.Library(name, [{"id": cid, "name": "s",
                                   "champion": champ,
                                   "skinlines": ["Squad Line"]}])
    five = [one("P1", "Aatrox", 266000), one("P2", "Elise", 60000),
            one("P3", "Ahri", 103000), one("P4", "Ashe", 22000),
            one("P5", "Aatrox", 266001)]   # P5 collides with P1 -> only 4 seat
    sl5 = [r for r in compute_suggestions(gd5, five, set(), {})
           if r["line"] == "Squad Line"]
    check("five-stack shows a 4/5 comp when a full comp is impossible",
          bool(sl5) and sl5[0]["seated"] == 4 and sl5[0]["total"] == 5
          and not sl5[0]["ok"])
    sl4 = [r for r in compute_suggestions(gd5, five[:4], set(), {})
           if r["line"] == "Squad Line"]
    check("four-stack shows a complete 4/4 comp (not a partial)",
          bool(sl4) and sl4[0]["ok"] and sl4[0]["seated"] == 4
          and sl4[0]["total"] == 4)

    # alt accounts (lobby): a line only the alt owns -> "needs a switch";
    # a line the current account owns stays "current".
    gd_alt = lsm.GameData({1: "BothLine", 2: "AltLine"},
                          {99: "Lux", 222: "Jinx", 86: "Garen"},
                          {"Lux": {"Mid"}, "Jinx": {"Bot"}, "Garen": {"Top"}})
    a_cur = {"player": "A", "skins": [{"id": 1, "name": "s",
             "champion": "Lux", "skinlines": ["BothLine"]}]}
    a_alt = {"player": "A-ALT", "skins": [{"id": 2, "name": "s",
             "champion": "Garen", "skinlines": ["AltLine"]}]}
    libA = lsm.parse_library(a_cur, gd_alt, display_name="A")
    libB = lsm.Library("B", [{"id": 3, "name": "s", "champion": "Jinx",
                              "skinlines": ["BothLine", "AltLine"]}])
    party = [{"puuid": "pA", "name": "A", "lib": libA},
             {"puuid": "pB", "name": "B", "lib": libB}]
    globals()["ALT_GROUPS"], saved_alt = [["pA", "pALT"]], ALT_GROUPS
    try:
        res_alt = compute_suggestions_with_alts(
            gd_alt, party, set(), {},
            lambda p: a_alt if p == "pALT" else None)
    finally:
        globals()["ALT_GROUPS"] = saved_alt
    by_line = {s["line"]: s for s in res_alt}
    check("alt: a line the current account owns stays 'current'",
          by_line.get("BothLine", {}).get("access") == "current")
    check("alt: an alt-only line is flagged 'switch' to the alt account",
          by_line.get("AltLine", {}).get("access") == "switch"
          and by_line["AltLine"].get("switchTo") == "A-ALT")
    alt_comp = by_line.get("AltLine", {}).get("comp") or []
    alt_players = {seat["player"] for seat in alt_comp}
    check("alt: switch card's comp/grid is labelled with the alt account",
          "A-ALT" in alt_players and "A" not in alt_players)

    # skin names: cells/comp carry the exact skin to equip (matters when the
    # skin name isn't just line + champion, e.g. Highstakes)
    gd_sk = lsm.GameData({1: "Highstakes"},
                         {134: "Syndra", 82: "Mordekaiser"},
                         {"Syndra": {"Mid"}, "Mordekaiser": {"Top"}})
    lp = lsm.Library("P", [{"id": 134001, "name": "Queen of Diamonds Syndra",
                            "champion": "Syndra", "skinlines": ["Highstakes"]}])
    lq = lsm.Library("Q", [{"id": 82001, "name": "King of Clubs Mordekaiser",
                            "champion": "Mordekaiser",
                            "skinlines": ["Highstakes"]}])
    hs = [r for r in compute_suggestions(gd_sk, [lp, lq], set(), {})
          if r["line"] == "Highstakes"]
    comp_skins = {seat.get("skin") for seat in hs[0]["comp"]} if hs else set()
    check("skin: comp carries the exact skin name",
          {"Queen of Diamonds Syndra",
           "King of Clubs Mordekaiser"} <= comp_skins)
    cell_skin = next((c.get("skin") for row in hs[0]["grid"]
                      for c in row["cells"]["Mid"] if c["champ"] == "Syndra"),
                     None) if hs else None
    check("skin: grid pick cell carries the skin name",
          cell_skin == "Queen of Diamonds Syndra")

    # fit_state: a huge state is trimmed to fit; a normal one is untouched
    big = fit_state({"aramMode": False, "suggestions": list(sug_all),
                     "aram": []}, budget=3000)
    check("fit_state trims oversized state under budget",
          len(json.dumps(big, sort_keys=True)) <= 3000
          and 0 < len(big["suggestions"]) < 20)
    small = {"aramMode": False, "suggestions": list(sug_all[:3]), "aram": []}
    fit_state(small, budget=STATE_BUDGET)
    check("fit_state leaves an in-budget state untouched",
          len(small["suggestions"]) == 3)

    # bans: read from completed ban actions when the bans lists are empty
    # (the live-game bug where picks showed but bans didn't)
    names = {266: "Aatrox", 60: "Elise", 99: "Lux", 22: "Ashe"}
    cs_actions = {"bans": {"myTeamBans": [], "theirTeamBans": []},
                  "actions": [[{"type": "ban", "completed": True,
                                "championId": 266},
                               {"type": "ban", "completed": True,
                                "championId": 60}],
                              [{"type": "pick", "completed": True,
                                "championId": 99}],
                              [{"type": "ban", "completed": False,
                                "championId": 22}]]}
    check("bans read from completed ban actions (lists empty)",
          read_bans(cs_actions, names) == ["Aatrox", "Elise"])
    cs_lists = {"bans": {"myTeamBans": [266], "theirTeamBans": [60]},
                "actions": []}
    check("bans still read from the bans lists when present",
          read_bans(cs_lists, names) == ["Aatrox", "Elise"])

    # role prefs: assignedPosition -> role, and steers the comp's seating
    cs_roles = {"myTeam": [{"puuid": "pa", "assignedPosition": "top"},
                           {"puuid": "pb", "assignedPosition": "utility"}]}
    rp = read_role_prefs(cs_roles, {"pa": "A", "pb": "B"})
    check("assignedPosition maps to role prefs",
          rp == {"A": ["Top"], "B": ["Support"]})
    # Ekko can play Top or Mid; with A pinned to Top-pref, the comp seats
    # Ekko top (not its mastery-default), leaving Mid for B.
    gd_rp = lsm.GameData({1: "Firelight"}, {245: "Ekko", 1: "Annie"},
                         {"Ekko": {"Mid", "Top"}, "Annie": {"Mid"}})
    ra = lsm.Library("A", [{"id": 245001, "name": "s", "champion": "Ekko",
                            "skinlines": ["Firelight"]}])
    rb = lsm.Library("B", [{"id": 1001, "name": "s", "champion": "Annie",
                            "skinlines": ["Firelight"]}])
    fl = [r for r in compute_suggestions(gd_rp, [ra, rb], set(), {},
                                         role_prefs={"A": ["Top"]})
          if r["line"] == "Firelight"]
    seat_a = next((s for s in fl[0]["comp"] if s["player"] == "A"), None) \
        if fl else None
    check("role prefs seat the player in their assigned lane",
          seat_a and seat_a["role"] == "Top")

    # regression: watch_loop must NOT bind a local `s` — its nested push()
    # closure calls the module-level s() Firestore helper, and a local `s`
    # (e.g. a stray `for s in ...`) silently breaks every push in lobby phase.
    check("watch_loop doesn't shadow the s() Firestore helper",
          "s" not in watch_loop.__code__.co_varnames)

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("All companion self tests passed.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="League Skin Matcher companion")
    parser.add_argument("command", nargs="?",
                        choices=["upload", "watch", "gui", "selftest"],
                        help="omit (or double-click the file) for the "
                             "mini app window")
    parser.add_argument("--dry-run", action="store_true",
                        help="don't touch Firestore; print what would "
                             "happen")
    args = parser.parse_args(argv)
    try:
        if args.command == "upload":
            do_upload(dry_run=args.dry_run)
        elif args.command == "watch":
            watch_loop(dry_run=args.dry_run)
        elif args.command == "selftest":
            return selftest()
        else:
            run_gui()
    except CompanionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
