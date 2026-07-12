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

function render(state) {
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
  const suggestions = state.suggestions || [];
  $("#empty").hidden = suggestions.length > 0;
  if (state.phase === "offline") {
    $("#empty").textContent =
      "The captain's League client is closed — live comps resume " +
      "when it's back.";
  } else if (!suggestions.length) {
    $("#empty").textContent =
      "Waiting for comps — needs at least two uploaded libraries " +
      "in the party.";
  }
  suggestions.forEach((sug) => {
    const card = el("div", "card" + (sug.ok ? "" : " blocked"));
    card.style.setProperty("--accent", sug.color || "#c8aa6e");
    const h = el("h3");
    h.append(el("span", "emoji", sug.emoji || ""));
    h.append(el("span", null, sug.line));
    card.append(h);
    (sug.comp || []).forEach((seat) => {
      const row = el("div", "seat");
      row.append(el("span", "role", seat.role));
      if (seat.champId) row.append(portrait(seat));
      row.append(el("span", null, seat.champ));
      row.append(el("span", "who", seat.player));
      card.append(row);
    });
    cards.append(card);
  });

  $("#status").textContent =
    `updated ${new Date().toLocaleTimeString()}`;
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
  suggestions: [
    {
      line: "Blood Moon", emoji: "👹", color: "#922b21", ok: true,
      comp: [
        { role: "Top", player: "StallionPrime#9125", champ: "Diana", champId: 131 },
        { role: "Jungle", player: "POG Fennel#68419", champ: "Talon", champId: 91 },
        { role: "Mid", player: "Jhin Blossoms#Jhin", champ: "Twisted Fate", champId: 4 },
        { role: "Bot", player: "RubixQber#ayaya", champ: "Sivir", champId: 15 },
        { role: "Support", player: "aesuki#sushi", champ: "Elise", champId: 60 },
      ],
    },
    {
      line: "High Noon", emoji: "🤠", color: "#e07b1f", ok: true,
      comp: [
        { role: "Top", player: "RubixQber#ayaya", champ: "Sion", champId: 14 },
        { role: "Jungle", player: "StallionPrime#9125", champ: "Rek'Sai", champId: 421 },
        { role: "Mid", player: "Jhin Blossoms#Jhin", champ: "Lucian", champId: 236 },
        { role: "Bot", player: "aesuki#sushi", champ: "Ashe", champId: 22 },
        { role: "Support", player: "POG Fennel#68419", champ: "Leona", champId: 89 },
      ],
    },
    { line: "Pool Party", emoji: "🏖", color: "#1fc3c3", ok: false, comp: null },
  ],
};

/* ---------------- boot ---------------- */

const params = new URLSearchParams(location.search);

if (params.get("demo")) {
  render(DEMO);
  $("#status").textContent = "demo mode — no Firebase connection";
} else if (params.get("party")) {
  const app = firebase.initializeApp(window.FIREBASE_CONFIG);
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
      <p class="hint">Want a preview right now? <a href="?demo=1">See
      the demo</a>.</p>
    </div>`;
}
