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

The current app is the **Companion** + web page (see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design).

## Running it

**Windows executable (no install):** run `release/LSMCompanion.exe`.
Upload your library once, then the captain keeps it open to feed the
live party page. SmartScreen may warn on an unsigned exe — "More info →
Run anyway".

**From source:** needs Python 3.9+ (python.org default install; standard
library only, no pip packages):

```
python release/lsm_companion.py
```

### Deprecated: the old offline desktop app

The original standalone tool (Skin Options + Team Builder tabs over
manually-traded JSON) is no longer shipped as an exe. Its code lives on
in `league_skin_matcher.py` — now the shared engine the Companion imports
— so you can still run it from source if you want the offline experience:

```
python release/league_skin_matcher.py            # the old desktop GUI
python release/league_skin_matcher.py --selftest # engine self tests
```

## Building the exe

```
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --icon reksai.ico --name LSMCompanion release/lsm_companion.py
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
