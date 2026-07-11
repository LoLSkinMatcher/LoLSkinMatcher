# LoLSkinMatcher

*Because we don't win unless it's in style.*

**v1.1 — made by StallionPrime**

Find the skinlines (High Noon, Pool Party, Star Guardian, …) that you and
your friends can **all play at the same time on different champions** —
without spending 15 minutes screen-sharing skin collections.

## How it works

1. Each friend runs the app, opens their League client, and clicks
   **Export My Skins…** — their owned skins (and champion mastery) are read
   from the League client's own local API and saved as a plain JSON file.
2. One person collects the files and adds them with **Add Friend's
   Library…** (friends are remembered between sessions).
3. The **Skin Options** tab shows every skinline color-coded: green means
   everyone owns it *and* can each play a different champion (click a row
   for a suggested lineup). The **Team Builder** tab (4–5 players) goes
   further and splits the group into Top / Jungle / Mid / Bot / Support.

Extras: per-player enable/disable tabs, "me" marking so the mastery filter
only narrows your own champion pool, account merging for people with
multiple accounts, a champion lock for the Team Builder, and per-skinline
emoji + color badges. Rek'Sai can be placed in all five roles. She plays
everywhere.

## Running it

**Option A — Windows executable (no install):** run
`release/LeagueSkinMatcher.exe`. SmartScreen may warn because it's an
unsigned hobby exe — "More info → Run anyway".

**Option B — from source:** needs Python 3.9+ (python.org default install;
standard library only, no pip packages):

```
python league_skin_matcher.py
```

Both are the same app — the exe is just the script bundled with a Python
interpreter via PyInstaller.

### CLI extras

```
python league_skin_matcher.py --export [FILE]   # export without the GUI
python league_skin_matcher.py --load a.json …   # open with files loaded
python league_skin_matcher.py --selftest        # run the built-in tests
```

## Building the exe

```
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --icon reksai.ico --name LeagueSkinMatcher league_skin_matcher.py
```

## Privacy / data

- Skin ownership is read from the League client's local API on
  `127.0.0.1` (the same API the client's own UI uses). Read-only; no
  passwords or tokens are touched. **Nothing is uploaded anywhere.**
- Skinline names come from [CommunityDragon](https://communitydragon.org)
  and champion role data from
  [Meraki Analytics](https://merakianalytics.com); both are cached for a
  week in `~/.league_skin_matcher_cache.json`.
- Added friends persist in `~/.league_skin_matcher_friends.json`.
  Deleting those two files is a full reset.

*League of Legends is a trademark of Riot Games. This is an unofficial fan
tool and is not endorsed by Riot Games.*
