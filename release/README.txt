LEAGUE SKIN MATCHER
===================
Find the skinlines (High Noon, Pool Party, Star Guardian, ...) that you
and your friends can ALL play at the same time on different champions.

There are TWO apps here. Most people just want the Companion.


COMPANION  (LSMCompanion.exe)  — v2.0, the party app
----------------------------------------------------
The easy way. No file trading, no setup — just the exe.

  1. Open the League client and log in.
  2. Run LSMCompanion.exe and click "Upload my library".
     (Re-run it whenever you buy new skins.)
  3. One person is the captain: they tick "Watch live lobby" and
     share the party link it shows.
  4. Everyone opens that link in a browser. The page shows, live, which
     skinlines your whole party can play — and during champ select it
     reacts to bans and enemy picks in real time.

That's it. Friends need ONLY LSMCompanion.exe (the group's settings are
built in). SmartScreen may warn on any unsigned exe — More info -> Run
anyway. The window title shows the version if you're asked.

The live page also has a no-setup preview:
https://lolskinmatcher.web.app/?demo=1


DESKTOP APP  (LeagueSkinMatcher.exe)  — v1.2, offline
-----------------------------------------------------
The original standalone tool — no internet accounts, no uploads. Export
your skins to a JSON file, collect your friends' files, and compare
them all in one window (Skin Options + Team Builder tabs). Use this if
you'd rather keep everything on your own PC and trade files by hand.


RUN FROM SOURCE INSTEAD (optional)
----------------------------------
Both apps are plain Python (3.9+, python.org default install; standard
library only). The .exe is just the script bundled with Python.

  python lsm_companion.py          (the Companion window)
  python league_skin_matcher.py    (the desktop app)


PRIVACY
-------
- Desktop app: nothing ever leaves your PC.
- Companion: your skin list (skins only, no passwords/tokens) syncs to
  the group's shared database so friends' apps can read it. Skin
  ownership is read from the League client's own local API; nothing
  else is collected.

League of Legends is a trademark of Riot Games. Unofficial fan tool,
not endorsed by Riot Games.
