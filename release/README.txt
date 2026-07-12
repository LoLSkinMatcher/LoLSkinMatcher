LEAGUE SKIN MATCHER
===================
Find the skinlines (High Noon, Pool Party, Star Guardian, ...) that you
and your friends can ALL play together — live, during champ select.

Just run LSMCompanion.exe. Nothing else to install.


COMPANION  (LSMCompanion.exe)  — the app
----------------------------------------
  1. Open the League client and log in.
  2. Run LSMCompanion.exe and click "Upload my library".
     (Re-run it whenever you buy new skins.)
  3. One person is the captain: they tick "Watch live lobby" and
     share the party link it shows.
  4. Everyone opens that link in a browser. The page shows, live, which
     skinlines your whole party can play — reacting to bans and enemy
     picks in draft, or the rolls + bench in ARAM.

Friends need ONLY LSMCompanion.exe (the group's settings are built in).
SmartScreen may warn on any unsigned exe — More info -> Run anyway.

No-setup preview of the party page:
  https://lolskinmatcher.web.app/?demo=1        (draft)
  https://lolskinmatcher.web.app/?demo=aram     (ARAM roulette)


RUN FROM SOURCE (optional)
--------------------------
Needs Python 3.9+ (python.org default install; standard library only).

  python lsm_companion.py      (the Companion window)


DEPRECATED: the old offline desktop app
---------------------------------------
The original standalone tool (Skin Options + Team Builder tabs, compared
manually-traded JSON files) is no longer shipped as an .exe. Its code
still lives in league_skin_matcher.py because that file is now the
shared engine the Companion is built on — so if you ever want the old
offline app, you can still launch it from source:

  python league_skin_matcher.py


PRIVACY
-------
Your skin list (skins only, no passwords/tokens) syncs to the group's
shared database so friends' apps can read it. Skin ownership is read
from the League client's own local API; nothing else is collected.

League of Legends is a trademark of Riot Games. Unofficial fan tool,
not endorsed by Riot Games.
