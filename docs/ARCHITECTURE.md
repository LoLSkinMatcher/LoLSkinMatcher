# v2.0 Architecture — Party Sync + Web App

Goal: nobody trades JSON files. Everyone's library syncs automatically,
and one pretty web page shows the whole party what skinline comps they
can play — live, during champ select.

```
 friend's PC                     Firebase                    everyone
┌───────────────┐   upload    ┌──────────────┐   subscribe  ┌─────────┐
│ lsm_companion.py  │ ──────────▶ │  Firestore   │ ◀─────────── │ web app │
│ (runs once)   │  libraries/ │              │   parties/   │ (browser)│
└───────────────┘   {puuid}   │  libraries/  │    {code}    └─────────┘
                              │  parties/    │
┌───────────────┐  push state │              │
│ captain's PC  │ ──────────▶ └──────────────┘
│ lsm_companion.py  │   reads LCU: lobby members, champ select
│ watch (stays  │   pulls party libraries, computes comps,
│  running)     │   reacts to bans/enemy picks live
└───────────────┘
```

## Pieces

- **`debug/lsm_companion.py upload`** — one-shot. Reads your skins + PUUID from the
  League client, uploads to `libraries/{puuid}`, exits. Run it again when
  you buy skins. This replaces trading JSON files.
- **`debug/lsm_companion.py watch`** — captain mode, the only long-running process.
  Polls the LCU every 3s: lobby members → fetches their libraries from
  Firestore → champ select session → removes banned + enemy-picked
  champions from everyone's pools, pins locked-in teammates → runs the
  same comp solver as the desktop app → pushes the whole state as one
  document to `parties/{captain puuid}`. Prints the shareable web link.
- **`webapp/`** — static page on Firebase Hosting. Anonymous auth +
  a realtime listener on the party document. Renders party chips, bans,
  enemy picks, and comp cards with champion icons. `?demo=1` shows
  canned data with no Firebase needed (UI development / screenshots).
- **The desktop app (v1.x)** keeps working unchanged for offline use.

## Identity & security

- **No sign-in**: Firebase Anonymous Auth. The companion creates a silent
  token on first run (cached in `~/.lsm_companion_auth.json`); the web page
  does the same in the browser. Nobody types anything.
- Players are keyed by **PUUID** (Riot's stable player id, read from the
  local client) — no usernames to manage, and lobby membership gives the
  captain exactly the right keys to fetch.
- `firestore.rules` requires auth for all access, enforces document
  shape, and caps sizes. Treat the data as semi-public: it's skin lists.
  The privacy pitch changes from "nothing leaves your PC" to "your skin
  list syncs to the group's database" — the companion must stay opt-in.
- The Firebase web `apiKey` is public by design; rules are the security
  boundary. Consider enabling App Check later.

## Setup (one person does this once)

1. console.firebase.google.com → Add project (free Spark plan).
2. Build → Authentication → Sign-in method → enable **Anonymous**.
3. Build → Firestore Database → create (production mode), then paste
   `firestore.rules` into the Rules tab.
4. Project settings → add a **Web app** → copy the config values into
   `firebase_config.json` (companion) and `webapp/config.js` (page).
5. Hosting: `npm i -g firebase-tools && firebase deploy` from the repo
   (or just open `webapp/index.html` locally while prototyping).

## Later phases / open questions

- Auto-run the upload companion on PC start (tray icon?), so libraries are
  always fresh without anyone thinking about it.
- Party codes shorter than a PUUID (hash prefix) for prettier links.
- Counter-pick-aware suggestions need a matchup data source — parked.
- In-game overlay (Overwolf-style) rejected for now: fragile, heavy,
  and the web page on a second monitor/phone covers the use case.
