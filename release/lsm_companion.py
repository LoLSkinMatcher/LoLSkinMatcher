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
MAX_SUGGESTIONS = 12
COMPANION_VERSION = "2.0"
APP_TITLE = f"LoLSkinMatcher Companion  v{COMPANION_VERSION}"

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
    order = [lib.player for lib in libraries]
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
        # a full comp is only possible if nobody has an empty pool
        comp = None
        if all(pools[p] for p in order):
            comp = lsm.find_team_comp(pools, gd.champ_positions, mastery)
        picks = {(p, role, champ)
                 for p, (champ, role) in (comp or {}).items()}

        # the 5x5 grid: for each player (row) and lane (column), every
        # champion they can play there, mastery-sorted, marking the
        # suggested pick
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
                     "pick": (player, role, c) in picks}
                    for c in champs]
            grid.append({"player": player, "cells": cells})

        emoji, color = lsm.style_for(line)
        out.append({
            "line": line, "emoji": emoji, "color": color,
            "ok": comp is not None,
            "comp": [{"role": role, "player": player, "champ": champ,
                      "champId": gd.champ_ids.get(champ)}
                     for player, (champ, role) in comp.items()]
            if comp else None,
            "grid": grid,
        })
    out.sort(key=lambda r: (not r["ok"], r["line"].lower()))
    return out[:MAX_SUGGESTIONS]


# --------------------------------------------------------------------------
# Core actions (shared by CLI and GUI)
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

            libs, missing, members = [], [], []
            for m in raw:
                puuid = m["puuid"]
                if puuid not in libraries_cache and not dry_run:
                    doc = fs_get(cfg, auth, f"libraries/{puuid}")
                    blob = field_str(doc, "data")
                    libraries_cache[puuid] = \
                        json.loads(blob) if blob else None
                name = resolve_name(puuid, m.get("name"))
                members.append({"puuid": puuid, "name": name})
                data = libraries_cache.get(puuid)
                if data:
                    libs.append(lsm.parse_library(data, gd,
                                                  display_name=name))
                else:
                    missing.append(name)

            cs = champ_select(lcu)
            phase = "champ select" if cs else "lobby"
            blocked, enemy_picks, pinned = set(), [], {}
            bans = []
            if cs:
                ban_ids = (cs.get("bans", {}).get("myTeamBans", [])
                           + cs.get("bans", {}).get("theirTeamBans", []))
                bans = [b for b in (gd.champ_names.get(cid)
                                    for cid in ban_ids) if b]
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

            suggestions = compute_suggestions(gd, libs, blocked, pinned) \
                if len(libs) >= 2 else []

            push({
                "phase": phase,
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
            })
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

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="League Skin Matcher companion")
    parser.add_argument("command", nargs="?",
                        choices=["upload", "watch", "gui"],
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
