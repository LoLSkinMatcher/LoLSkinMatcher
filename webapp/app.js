/* LoLSkinMatcher party page.
   ?party=<captain puuid>  -> live Firestore subscription
   ?demo=1                 -> canned data, no Firebase needed */

const ICON = (id) =>
  `https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/` +
  `global/default/v1/champion-icons/${id}.png`;

const $ = (sel) => document.querySelector(sel);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text) node.textContent = text;
  return node;
}

function portrait(entry, banned) {
  const img = el("img");
  img.src = ICON(entry.champId);
  img.title = entry.champ;
  img.alt = entry.champ;
  if (banned) img.classList.add("banned");
  return img;
}

/* { base, lines: { skinline: { "_": defaultPath, champ: path } } } — the
   splash-art wallpapers per skinline per champion (loaded at boot). */
let ART = { base: "", lines: {} };

/* Splash URL for a skinline, preferring the featured champion's own skin
   in that line (so the banner reflects who's actually in the comp), else
   the line's default. */
function artUrl(line, champ) {
  const slot = ART.lines && ART.lines[line];
  if (!slot) return null;
  const rel = (champ && slot[champ]) || slot["_"];
  return rel ? ART.base + rel : null;
}

/* A card with a splash-art banner header. Returns {card, body, banner};
   append content to `body`. Falls back to the accent color when no art. */
function makeCard(line, color, extraClass, featureChamp) {
  const card = el("div", "card" + (extraClass ? " " + extraClass : ""));
  card.style.setProperty("--accent", color || "#c8aa6e");
  const banner = el("div", "banner");
  const url = artUrl(line, featureChamp);
  if (url) {
    banner.style.backgroundImage = `url("${url}")`;
  } else {
    banner.classList.add("banner-plain");
  }
  banner.append(el("span", "banner-title", line));
  card.append(banner);
  const body = el("div", "card-body");
  card.append(body);
  return { card, body, banner };
}

/* ---------------- champion filter (local to this page) ---------------- */

let lastState = null;   // last rendered state, so filter changes can re-render
let lastStamp = "";     // "updated" time, kept across filter-only re-renders

function champsInSuggestion(sug) {
  const names = new Set();
  (sug.comp || []).forEach((s) => s.champ && names.add(s.champ));
  (sug.grid || []).forEach((row) => {
    Object.values(row.cells || {}).forEach((list) =>
      (list || []).forEach((c) => c.champ && names.add(c.champ)));
  });
  return names;
}

function champsInAram(r) {
  const names = new Set();
  (r.assignment || []).forEach((s) => s.champ && names.add(s.champ));
  return names;
}

function activeFilter() {
  return $("#champ-filter").value.trim().toLowerCase();
}
function filterText() {
  return $("#champ-filter").value.trim();
}
function matchesFilter(names, filter) {
  if (!filter) return true;
  for (const n of names) if (n.toLowerCase().includes(filter)) return true;
  return false;
}

/* fill the autocomplete with every champion playable in the current data,
   without clobbering the list (and the user's typing) when unchanged */
function populateChampList(names) {
  const uniq = [...new Set(names)].sort((a, b) => a.localeCompare(b));
  const list = $("#champ-list");
  const sig = uniq.join("|");
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  list.replaceChildren();
  uniq.forEach((n) => {
    const opt = document.createElement("option");
    opt.value = n;
    list.append(opt);
  });
}

function setStatus(state, keepStamp) {
  if (!keepStamp) lastStamp = new Date().toLocaleTimeString();
  const ver = state.companionVersion
    ? `  ·  captain's companion v${state.companionVersion}` : "";
  $("#status").textContent = `updated ${lastStamp}${ver}`;
}

function render(state, keepStamp) {
  lastState = state;
  $("#phase").textContent = state.phase || "lobby";

  const members = $("#members");
  members.replaceChildren();
  (state.members || []).forEach((m) => {
    const missing = (state.missing || []).includes(m.name);
    const chip = el("div", "chip" + (missing ? " missing" : ""));
    chip.append(el("span", "dot"));
    chip.append(el("span", null, m.name));
    if (missing) chip.append(el("span", "note", "no library yet"));
    if (state.pinned && state.pinned[m.name])
      chip.append(el("span", "note", `locked ${state.pinned[m.name]}`));
    members.append(chip);
  });

  const hasDraft = (state.bans || []).length ||
    (state.enemyPicks || []).length;
  $("#draft-section").hidden = !hasDraft;
  const bans = $("#bans");
  bans.replaceChildren();
  (state.bans || []).forEach((b) => bans.append(portrait(b, true)));
  if (!(state.bans || []).length) bans.append(el("span", "none", "none"));
  const enemy = $("#enemy");
  enemy.replaceChildren();
  (state.enemyPicks || []).forEach((e) => enemy.append(portrait(e)));
  if (!(state.enemyPicks || []).length)
    enemy.append(el("span", "none", "none"));

  const cards = $("#cards");
  cards.replaceChildren();

  // ARAM roulette mode: no lanes, just who can style from the current
  // rolls + shared bench.
  if (state.aramMode) {
    $("#section-title").textContent = "ARAM skinline roulette 🎲";
    cards.className = "cards aram-cards";
    const allAram = state.aram || [];
    populateChampList(allAram.flatMap((r) => [...champsInAram(r)]));
    $("#filter").hidden = allAram.length === 0;
    const filter = activeFilter();
    const aram = allAram.filter((r) => matchesFilter(champsInAram(r), filter));
    $("#empty").hidden = aram.length > 0;
    if (!aram.length) {
      $("#empty").textContent = filter && allAram.length
        ? `No current roll lets ${filterText()} style — reroll, grab from `
          + "the bench, or clear the filter."
        : "No skinline matches from the current rolls — reroll or grab "
          + "from the bench and check again!";
    }
    aram.forEach((r) => {
      const feature = r.assignment[0] && r.assignment[0].champ;
      const { card, body, banner } = makeCard(
        r.line, r.color, r.full ? "aram-full" : "", feature);
      banner.append(el("span", "banner-badge",
        r.full ? `🎉 all ${r.total} can style!` : `${r.count}/${r.total}`));
      r.assignment.forEach((seat) => {
        const row = el("div", "seat");
        if (seat.champId) row.append(portrait(seat));
        row.append(el("span", null, seat.champ));
        row.append(el("span", "src",
          seat.source === "rolled" ? "rolled" : "from bench"));
        row.append(el("span", "who", seat.player));
        body.append(row);
      });
      cards.append(card);
    });
    setStatus(state, keepStamp);
    return;
  }

  $("#section-title").textContent = "Skinline comps you can still play";
  cards.className = "cards";
  const allSug = state.suggestions || [];
  populateChampList(allSug.flatMap((s) => [...champsInSuggestion(s)]));
  $("#filter").hidden = allSug.length === 0;
  const draftFilter = activeFilter();
  const suggestions = allSug.filter(
    (s) => matchesFilter(champsInSuggestion(s), draftFilter));
  $("#empty").hidden = suggestions.length > 0;
  if (state.phase === "offline") {
    $("#empty").textContent =
      "The captain's League client is closed — live comps resume " +
      "when it's back.";
  } else if (!allSug.length) {
    $("#empty").textContent =
      "Waiting for comps — needs at least two uploaded libraries " +
      "in the party.";
  } else if (!suggestions.length) {
    $("#empty").textContent =
      `No current comp fits ${filterText()} — try another champion ` +
      "or clear the filter.";
  }
  const ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"];
  const ROLE_SHORT = { Top: "Top", Jungle: "JG", Mid: "Mid", Bot: "Bot",
                       Support: "Sup" };

  suggestions.forEach((sug) => {
    // banner reflects the suggested comp's first pick, so it matches
    // who's actually being played
    const feature = sug.comp && sug.comp[0] && sug.comp[0].champ;
    const { card, body } = makeCard(
      sug.line, sug.color, sug.ok ? "" : "blocked", feature);

    // 5x5 grid: players (rows) x lanes (columns), every champion each
    // player can play in that lane; the suggested pick is highlighted
    const grid = sug.grid || [];
    if (grid.length) {
      const table = el("table", "grid");
      const head = el("tr");
      head.append(el("th", "corner", "Player"));
      ROLES.forEach((r) => head.append(el("th", null, ROLE_SHORT[r])));
      table.append(head);
      grid.forEach((row) => {
        const tr = el("tr");
        tr.append(el("td", "pname", row.player));
        ROLES.forEach((role) => {
          const td = el("td", "cell");
          const champs = (row.cells && row.cells[role]) || [];
          if (!champs.length) {
            td.append(el("span", "dash", "·"));
          } else {
            champs.forEach((c) => {
              const chip = el("span", "gchip" + (c.pick ? " pick" : ""));
              if (c.champId) chip.append(portrait(c));
              chip.append(el("span", "gname", c.champ));
              chip.title = c.champ + (c.pick ? " (suggested)" : "");
              td.append(chip);
            });
          }
          tr.append(td);
        });
        table.append(tr);
      });
      const wrap = el("div", "grid-wrap");
      wrap.append(table);
      body.append(wrap);
    } else if (sug.comp && sug.comp.length) {
      // no lane grid in this data (older companion) — show the
      // suggested lineup it does provide, as a normal result
      body.append(el("p", "cardnote", "Suggested lineup"));
      sug.comp.forEach((seat) => {
        const row = el("div", "seat");
        row.append(el("span", "role", seat.role));
        if (seat.champId) row.append(portrait(seat));
        row.append(el("span", null, seat.champ));
        row.append(el("span", "who", seat.player));
        body.append(row);
      });
    } else if (!sug.ok) {
      body.append(el("p", "cardnote",
        "Owned by everyone, but no full role split is possible."));
    }
    cards.append(card);
  });

  setStatus(state, keepStamp);
}

/* re-render (keeping the "updated" time) when the champion filter changes */
(function wireFilter() {
  const input = $("#champ-filter");
  const clear = $("#filter-clear");
  input.addEventListener("input", () => {
    clear.hidden = !input.value.trim();
    if (lastState) render(lastState, true);
  });
  clear.addEventListener("click", () => {
    input.value = "";
    clear.hidden = true;
    input.focus();
    if (lastState) render(lastState, true);
  });
})();

/* build a grid from a comp + extra per-cell champions (demo helper) */
function demoGrid(players, comp, extra) {
  const ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"];
  const pickBy = {};
  (comp || []).forEach((s) => { pickBy[s.player] = s; });
  return players.map((p) => {
    const cells = {};
    ROLES.forEach((role) => { cells[role] = []; });
    const seat = pickBy[p.name];
    if (seat) cells[seat.role].push(
      { champ: seat.champ, champId: seat.champId, pick: true });
    ((extra && extra[p.name]) || []).forEach((e) =>
      cells[e.role].push({ champ: e.champ, champId: e.champId }));
    return { player: p.name, cells };
  });
}

/* ---------------- demo mode ---------------- */

const DEMO = {
  phase: "champ select",
  members: [
    { name: "Jhin Blossoms#Jhin" }, { name: "POG Fennel#68419" },
    { name: "RubixQber#ayaya" }, { name: "aesuki#sushi" },
    { name: "StallionPrime#9125" },
  ],
  missing: ["aesuki#sushi"],
  pinned: { "POG Fennel#68419": "Talon" },
  bans: [
    { champ: "Yasuo", champId: 157 }, { champ: "Zed", champId: 238 },
    { champ: "Blitzcrank", champId: 53 },
  ],
  enemyPicks: [
    { champ: "Jinx", champId: 222 }, { champ: "Thresh", champId: 412 },
  ],
  suggestions: [],  // filled in below
};

const DEMO_PLAYERS = DEMO.members;
DEMO.suggestions = [
  (() => {
    const comp = [
      { role: "Top", player: "StallionPrime#9125", champ: "Diana", champId: 131 },
      { role: "Jungle", player: "POG Fennel#68419", champ: "Talon", champId: 91 },
      { role: "Mid", player: "Jhin Blossoms#Jhin", champ: "Twisted Fate", champId: 4 },
      { role: "Bot", player: "RubixQber#ayaya", champ: "Sivir", champId: 15 },
      { role: "Support", player: "aesuki#sushi", champ: "Elise", champId: 60 },
    ];
    return {
      line: "Blood Moon", emoji: "👹", color: "#922b21", ok: true, comp,
      grid: demoGrid(DEMO_PLAYERS, comp, {
        "StallionPrime#9125": [{ role: "Mid", champ: "Diana", champId: 131 },
                               { role: "Jungle", champ: "Rek'Sai", champId: 421 }],
        "Jhin Blossoms#Jhin": [{ role: "Support", champ: "Pyke", champId: 555 }],
        "RubixQber#ayaya": [{ role: "Bot", champ: "Kalista", champId: 429 }],
        "aesuki#sushi": [{ role: "Support", champ: "Thresh", champId: 412 },
                         { role: "Mid", champ: "Zilean", champId: 26 }],
      }),
    };
  })(),
  (() => {
    const comp = [
      { role: "Top", player: "RubixQber#ayaya", champ: "Sion", champId: 14 },
      { role: "Jungle", player: "StallionPrime#9125", champ: "Rek'Sai", champId: 421 },
      { role: "Mid", player: "Jhin Blossoms#Jhin", champ: "Lucian", champId: 236 },
      { role: "Bot", player: "aesuki#sushi", champ: "Ashe", champId: 22 },
      { role: "Support", player: "POG Fennel#68419", champ: "Leona", champId: 89 },
    ];
    return {
      line: "High Noon", emoji: "🤠", color: "#e07b1f", ok: true, comp,
      grid: demoGrid(DEMO_PLAYERS, comp, {
        "StallionPrime#9125": [{ role: "Top", champ: "Rek'Sai", champId: 421 }],
        "Jhin Blossoms#Jhin": [{ role: "Bot", champ: "Lucian", champId: 236 }],
        "RubixQber#ayaya": [{ role: "Support", champ: "Thresh", champId: 412 }],
      }),
    };
  })(),
  {
    line: "Pool Party", emoji: "🏖", color: "#1fc3c3", ok: false,
    comp: null,
    grid: demoGrid(DEMO_PLAYERS, null, {
      "Jhin Blossoms#Jhin": [{ role: "Bot", champ: "Miss Fortune", champId: 21 }],
      "POG Fennel#68419": [{ role: "Bot", champ: "Miss Fortune", champId: 21 }],
      "RubixQber#ayaya": [{ role: "Bot", champ: "Miss Fortune", champId: 21 }],
    }),
  },
];

const DEMO_ARAM = {
  phase: "champ select",
  aramMode: true,
  members: [
    { name: "Mike Oxmaul#NA5" }, { name: "StallionPrime#9125" },
    { name: "aesuki#sushi" },
  ],
  missing: [],
  aram: [
    {
      line: "Star Guardian", emoji: "⭐", color: "#f2c94c",
      count: 3, total: 3, full: true,
      assignment: [
        { player: "Mike Oxmaul#NA5", champ: "Lux", champId: 99, source: "rolled" },
        { player: "StallionPrime#9125", champ: "Jinx", champId: 222, source: "bench" },
        { player: "aesuki#sushi", champ: "Soraka", champId: 16, source: "rolled" },
      ],
    },
    {
      line: "Pool Party", emoji: "🏖", color: "#1fc3c3",
      count: 2, total: 3, full: false,
      assignment: [
        { player: "Mike Oxmaul#NA5", champ: "Miss Fortune", champId: 21, source: "bench" },
        { player: "StallionPrime#9125", champ: "Draven", champId: 119, source: "rolled" },
      ],
    },
  ],
};

/* ---------------- boot ---------------- */

const params = new URLSearchParams(location.search);

async function loadArt() {
  try {
    ART = await (await fetch("skinline_art.json")).json();
  } catch (e) {
    ART = {};   // splash art is cosmetic; fall back to accent colors
  }
}

if (params.get("demo") === "aram") {
  loadArt().then(() => render(DEMO_ARAM));
  $("#status").textContent = "demo mode (ARAM) — no Firebase connection";
} else if (params.get("demo")) {
  loadArt().then(() => render(DEMO));
  $("#status").textContent = "demo mode — no Firebase connection";
} else if (params.get("party")) {
  loadArt().then(() => {
    firebase.initializeApp(window.FIREBASE_CONFIG);
    firebase.auth().signInAnonymously().then(() => {
      firebase.firestore()
        .collection("parties").doc(params.get("party"))
        .onSnapshot((doc) => {
          if (!doc.exists) {
            $("#empty").hidden = false;
            $("#empty").textContent =
              "No party found — is the captain's companion running?";
            return;
          }
          render(JSON.parse(doc.data().state || "{}"));
        });
    }).catch((err) => {
      $("#status").textContent = `auth error: ${err.message}`;
    });
  });
} else {
  $("#phase").textContent = "no party";
  $("#party-section").hidden = true;
  $("#empty").hidden = false;
  $("#empty").innerHTML = `
    <div class="howto">
      <h3>How this works</h3>
      <ol>
        <li><b>Everyone, once:</b> open the League client and run the
          uploader — your skin library syncs automatically. Re-run it
          when you buy skins.</li>
        <li><b>The captain, each session:</b> run the companion in watch
          mode before queueing. It prints this page's link with your
          party code.</li>
        <li><b>Everyone:</b> open that link. This page follows your
          lobby and champ select live — bans and enemy picks update
          the comps in real time.</li>
      </ol>
      <p class="hint"><a class="dl"
        href="https://github.com/LoLSkinMatcher/LoLSkinMatcher/raw/main/release/LSMCompanion.exe"
        download>⬇ Download the companion (.exe)</a></p>
      <p class="hint">Want a preview right now? <a href="?demo=1">See
      the demo</a>.</p>
    </div>`;
}
