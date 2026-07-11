LEAGUE SKIN MATCHER
===================
Find the skinlines (High Noon, Pool Party, Star Guardian, ...) that you and
your friends can ALL play at the same time on different champions.

TWO WAYS TO RUN IT — PICK ONE, THEY ARE THE SAME APP
----------------------------------------------------
1) LeagueSkinMatcher.exe
   Double-click. Needs nothing installed. Windows SmartScreen may warn
   because it's an unsigned homemade exe — "More info" -> "Run anyway".

2) league_skin_matcher.py   (for the skeptical: it's plain readable code)
   Needs Python 3.9+ from python.org (default install settings are fine).
   Then double-click the file, or run:  python league_skin_matcher.py
   The .exe is nothing more than this exact file bundled together with a
   Python interpreter, so behavior is identical either way.

HOW TO USE
----------
1) Open your League client and log in.
2) Run this app, click "Export My Skins...", save the JSON file.
3) Send that JSON to whoever is comparing. They click
   "Add Friend's Library...", pick your file, give it a name — done.
   Friends are remembered between sessions; green rows = lines everyone
   can play together (click a row for a suggested lineup).
4) With 4-5 players added, check the TEAM BUILDER tab: it shows the
   skinlines where your whole group can queue up at once — a different
   champion for everyone, each in a different role (Top/Jungle/Mid/Bot/
   Support). Role flexes are generous on purpose.

WHAT IT TOUCHES (AND DOESN'T)
-----------------------------
- Reads your owned skins + champion mastery from the League client's own
  local API on 127.0.0.1 (the same one the client's UI uses). Read-only.
- Downloads public skinline names from CommunityDragon (a community mirror
  of the game's data files) and caches them for a week.
- Saves two small files in your user folder:
      ~\.league_skin_matcher_friends.json   (your added friends)
      ~\.league_skin_matcher_cache.json     (skinline catalog cache)
- Uploads NOTHING anywhere. No passwords or login tokens are read.
  Exports are plain JSON you can open in Notepad and inspect yourself.
