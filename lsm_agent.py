#!/usr/bin/env python3
"""
League Skin Matcher — Sync Agent (v2.0 prototype)

The background half of the web app. Two jobs:

  python lsm_agent.py upload
      Read your owned skins from the League client, upload them to the
      group's Firestore database (keyed by your PUUID), then exit.
      Friends run this once (and again whenever they buy skins).

  python lsm_agent.py watch
      Captain mode. Keeps running: watches your League lobby and champ
      select via the local client API, pulls party members' libraries
      from Firestore, computes which skinline comps are still possible
      (bans and enemy picks removed live), and pushes everything to
      Firestore for the web app to display. Prints the web link on start.

  Add --dry-run to either command to see what would be sent without
  touching Firestore (no config needed).

Setup: copy firebase_config.example.json to firebase_config.json and
fill in your Firebase project's apiKey + projectId. Uses Firebase
ANONYMOUS auth — no account, no sign-in; a silent token is created on
first run and cached in your user folder.

Standard library only, same as the main app.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import league_skin_matcher as lsm

CONFIG_PATH = Path(__file__).with_name("firebase_config.json")
AUTH_CACHE = Path.home() / ".lsm_agent_auth.json"
POLL_SECONDS = 3
MAX_SUGGESTIONS = 12


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
    if not CONFIG_PATH.is_file():
        raise SystemExit(
            f"Missing {CONFIG_PATH.name}. Copy "
            "firebase_config.example.json to firebase_config.json and "
            "fill in your Firebase project's apiKey and projectId.")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not cfg.get("apiKey") or not cfg.get("projectId"):
        raise SystemExit("firebase_config.json needs both apiKey and "
                         "projectId.")
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


# --------------------------------------------------------------------------
# LCU helpers (on top of the main app's client discovery)
# --------------------------------------------------------------------------

def require_lcu():
    lcu = lsm.find_lcu()
    if lcu is None:
        raise SystemExit("No running League client found — open League, "
                         "log in, and try again.")
    return lcu


def lobby_members(lcu):
    """[{puuid, name}] for the current lobby, or None if not in one."""
    try:
        lobby = lcu.get("/lol-lobby/v2/lobby")
    except Exception:
        return None
    members = []
    for m in lobby.get("members", []):
        if m.get("puuid"):
            members.append({
                "puuid": m["puuid"],
                "name": m.get("gameName") or m.get("summonerName")
                or "Unknown"})
    return members or None


def champ_select(lcu):
    try:
        return lcu.get("/lol-champ-select/v1/session")
    except Exception:
        return None


# --------------------------------------------------------------------------
# Suggestions (reuses the main app's solver, minus banned/taken champs)
# --------------------------------------------------------------------------

def compute_suggestions(gd, libraries, blocked_names, pinned):
    """Skinline comps still possible for these players.

    blocked_names: champions removed from everyone's pools (bans + enemy
    picks). pinned: {player_name: champion} for teammates already locked
    in — lines that can't seat their lock are dropped.
    """
    matrix = lsm.build_matrix(libraries)
    mastery = {lib.player: lib.mastery for lib in libraries}
    out = []
    for line, per_player in matrix.items():
        if len(per_player) != len(libraries):
            continue
        pools = {}
        impossible = False
        for player, champs in per_player.items():
            pool = [c for c in champs if c not in blocked_names]
            lock = pinned.get(player)
            if lock:
                pool = [lock] if lock in pool else []
            if not pool:
                impossible = True
                break
            pools[player] = pool
        if impossible:
            continue
        comp = lsm.find_team_comp(pools, gd.champ_positions, mastery)
        emoji, color = lsm.style_for(line)
        out.append({
            "line": line, "emoji": emoji, "color": color,
            "ok": comp is not None,
            "comp": [{"role": role, "player": player, "champ": champ,
                      "champId": gd.champ_ids.get(champ)}
                     for player, (champ, role) in comp.items()]
            if comp else None,
        })
    out.sort(key=lambda r: (not r["ok"], r["line"].lower()))
    return out[:MAX_SUGGESTIONS]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_upload(args):
    lcu = require_lcu()
    summoner = lcu.get("/lol-summoner/v1/current-summoner")
    puuid = summoner.get("puuid")
    if not puuid:
        raise SystemExit("Couldn't read your PUUID from the client.")
    gd = lsm.load_game_data(print)
    export = lsm.export_my_skins(gd)
    blob = json.dumps(export)
    print(f"{export['player']}: {len(export['skins'])} skins, "
          f"{len(blob) / 1024:.0f} KB")
    if args.dry_run:
        print(f"[dry-run] would write libraries/{puuid}")
        return
    cfg = load_config()
    auth = sign_in(cfg)
    fs_set(cfg, auth, f"libraries/{puuid}", {
        "player": s(export["player"]),
        "puuid": s(puuid),
        "updatedAt": s(now_iso()),
        "data": s(blob),
    })
    print("Uploaded! Your friends' captain can now pull your library "
          "automatically. Re-run this after you buy new skins.")


def cmd_watch(args):
    cfg = auth = None
    if not args.dry_run:
        cfg = load_config()
        auth = sign_in(cfg)
    lcu = require_lcu()
    summoner = lcu.get("/lol-summoner/v1/current-summoner")
    captain = summoner.get("puuid")
    gd = lsm.load_game_data(print)
    party_path = f"parties/{captain}"
    if not args.dry_run and cfg.get("webUrl"):
        print(f"\nParty page: {cfg['webUrl']}?party={captain}\n")
    libraries_cache = {}
    last_pushed = None
    print("Watching your lobby — Ctrl+C to stop.")

    while True:
        try:
            members = lobby_members(lcu) or [{
                "puuid": captain,
                "name": summoner.get("gameName") or "You"}]

            libs, missing = [], []
            for m in members:
                if m["puuid"] not in libraries_cache and not args.dry_run:
                    doc = fs_get(cfg, auth, f"libraries/{m['puuid']}")
                    blob = field_str(doc, "data")
                    libraries_cache[m["puuid"]] = \
                        json.loads(blob) if blob else None
                data = libraries_cache.get(m["puuid"])
                if data:
                    libs.append(lsm.parse_library(data, gd,
                                                  display_name=m["name"]))
                else:
                    missing.append(m["name"])

            cs = champ_select(lcu)
            phase = "champ select" if cs else "lobby"
            blocked, enemy_picks, pinned = set(), [], {}
            if cs:
                ban_ids = (cs.get("bans", {}).get("myTeamBans", [])
                           + cs.get("bans", {}).get("theirTeamBans", []))
                bans = [gd.champ_names.get(cid) for cid in ban_ids]
                bans = [b for b in bans if b]
                enemy_ids = [t.get("championId")
                             for t in cs.get("theirTeam", [])
                             if t.get("championId")]
                enemy_picks = [gd.champ_names.get(cid)
                               for cid in enemy_ids
                               if gd.champ_names.get(cid)]
                blocked = set(bans) | set(enemy_picks)
                name_by_puuid = {m["puuid"]: m["name"] for m in members}
                for t in cs.get("myTeam", []):
                    champ = gd.champ_names.get(t.get("championId") or 0)
                    player = name_by_puuid.get(t.get("puuid"))
                    if champ and player:
                        pinned[player] = champ
            else:
                bans = []

            suggestions = compute_suggestions(gd, libs, blocked, pinned) \
                if len(libs) >= 2 else []

            state = {
                "phase": phase,
                "members": members,
                "missing": missing,
                "bans": [{"champ": b, "champId": gd.champ_ids.get(b)}
                         for b in bans],
                "enemyPicks": [{"champ": e,
                                "champId": gd.champ_ids.get(e)}
                               for e in enemy_picks],
                "pinned": pinned,
                "suggestions": suggestions,
            }
            snapshot = json.dumps(state, sort_keys=True)
            if snapshot != last_pushed:
                if args.dry_run:
                    print(f"[dry-run] {phase}: {len(members)} member(s), "
                          f"{len(suggestions)} suggestion(s), "
                          f"missing: {missing or 'none'}")
                else:
                    fs_set(cfg, auth, party_path, {
                        "state": s(snapshot),
                        "captain": s(captain),
                        "updatedAt": s(now_iso()),
                    })
                    print(f"pushed: {phase}, {len(members)} member(s), "
                          f"{len(suggestions)} playable line(s)")
                last_pushed = snapshot
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"(retrying) {exc}")
        time.sleep(POLL_SECONDS)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="League Skin Matcher sync agent")
    parser.add_argument("command", choices=["upload", "watch"])
    parser.add_argument("--dry-run", action="store_true",
                        help="don't touch Firestore; print what would "
                             "happen")
    args = parser.parse_args(argv)
    try:
        if args.command == "upload":
            cmd_upload(args)
        else:
            cmd_watch(args)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
