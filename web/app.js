/* PlanTER — SPA statique : recherche, résultats, détail (§8.2). */
"use strict";

const $ = (sel) => document.querySelector(sel);

/* T7 v2.2 — en web, l''API est servie à la même origine (''/v1/*'') ;
   dans le shell natif Capacitor (WebView), on appelle l''API publique. */
const IS_CAPACITOR = typeof window.Capacitor !== "undefined"
  && typeof window.Capacitor.isNativePlatform === "function"
  && window.Capacitor.isNativePlatform();
const API_BASE = IS_CAPACITOR ? "https://ter.zvz.fr" : "";

/* ----------------------------------------------------------- PoW (anti-abus) */
const PoW = (() => {
  let _cache = null; // {salt, difficulty, ts}

  async function getChallenge() {
    if (_cache && (Date.now() - _cache.ts < 50_000)) return _cache;
    const r = await fetch(API_BASE + "/v1/challenge");
    if (!r.ok) throw new Error("challenge_failed");
    const c = await r.json();
    _cache = { salt: c.salt, difficulty: c.difficulty, ts: Date.now() };
    return _cache;
  }

  function sha256hex(str) {
    // SubtleCrypto est async, mais on fait un fallback sync pour les small difficulty
    // Utilisation d'un Web Worker serait idéal mais gardons simple pour l'instant
    const enc = new TextEncoder().encode(str);
    return crypto.subtle.digest("SHA-256", enc).then(buf =>
      Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("")
    );
  }

  async function solve(salt, difficulty) {
    const prefix = "0".repeat(difficulty);
    for (let nonce = 0; nonce < 1_000_000; nonce++) {
      const h = await sha256hex(salt + ":" + nonce);
      if (h.startsWith(prefix)) return String(nonce);
    }
    throw new Error("pow_unsolvable");
  }

  async function headers() {
    const ch = await getChallenge();
    const nonce = await solve(ch.salt, ch.difficulty);
    return {
      "X-PoW-Salt": ch.salt,
      "X-PoW-Nonce": nonce,
      "X-PoW-Difficulty": String(ch.difficulty),
    };
  }

  function invalidate() { _cache = null; }
  return { headers, invalidate };
})();

/* -------------------------------------------------------- Crypto (chiffrement hybride AES-GCM + RSA-OAEP) */
const Crypto_ = (() => {
  let _pubKey = null;

  function pemToBinary(pem) {
    const b64 = pem.replace(/-----.*-----/g, "").replace(/\s+/g, "");
    const raw = atob(b64);
    const buf = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
    return buf;
  }

  async function fetchKey() {
    if (_pubKey) return _pubKey;
    const r = await fetch(API_BASE + "/v1/crypto/pubkey");
    if (!r.ok) throw new Error("crypto_pubkey_failed");
    const { public_key } = await r.json();
    const der = pemToBinary(public_key);
    _pubKey = await crypto.subtle.importKey(
      "spki", der.buffer, { name: "RSA-OAEP", hash: "SHA-256" }, false, ["encrypt"]
    );
    return _pubKey;
  }

  async function encrypt(plaintext) {
    const key = await fetchKey();
    const aesKey = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt"]);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const enc = new TextEncoder().encode(JSON.stringify(plaintext));
    const cipherBuf = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, aesKey, enc);
    const aesRaw = new Uint8Array(await crypto.subtle.exportKey("raw", aesKey));
    const rsaBuf = await crypto.subtle.encrypt({ name: "RSA-OAEP" }, key, aesRaw);
    const result = new Uint8Array(rsaBuf.byteLength + iv.byteLength + cipherBuf.byteLength);
    result.set(new Uint8Array(rsaBuf), 0);
    result.set(iv, rsaBuf.byteLength);
    result.set(new Uint8Array(cipherBuf), rsaBuf.byteLength + iv.byteLength);
    let binary = "";
    result.forEach(b => binary += String.fromCharCode(b));
    return btoa(binary);
  }

  return { encrypt };
})();

const form = $("#search-form");
const fromInput = $("#from");
const toInput = $("#to");
const resultsSection = $("#results");
const journeysList = $("#journeys");
const noResults = $("#no-results");
const searchError = $("#search-error");
const detailSection = $("#detail");
const detailBody = $("#detail-body");
const searchButton = $("#search-button");

let lastJourneys = [];

/* ------------------------------------------------------------- dates (T5) */
function initDateRange() {
  fetch(API_BASE + "/v1/health")
    .then((r) => r.json())
    .then((h) => {
      const d = $("#date");
      d.min = h.coverage_start;
      d.max = h.coverage_end;
      const today = new Date();
      const iso = today.toISOString().slice(0, 10);
      d.value = iso < h.coverage_start ? h.coverage_start
        : iso > h.coverage_end ? h.coverage_end : iso;
    })
    .catch(() => {});
}

/* ------------------------------------------------------------- autocomplete */
/* Recherche en POST (pas de query string → ni log nginx, ni cache SW) ;
   résultats en sections titrées : « toutes les gares », Gares, Communes
   (villes sans gare, résolues vers les gares proches), Arrêts de bus. */
function setupAutocomplete(inputEl, listEl) {
  let timer = null;
  let items = [];

  function render() {
    listEl.innerHTML = "";
    items.forEach((s, i) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", "false");
      li.id = `${inputEl.id}-opt-${i}`;
      if (s.header) {
        li.className = "ac-section";
        li.textContent = s.header;
        listEl.appendChild(li);
        return;
      }
      if (s.sub) {
        const main = document.createElement("span");
        main.className = "ac-main";
        main.textContent = s.name;
        const sub = document.createElement("span");
        sub.className = "ac-sub";
        sub.textContent = s.sub;
        li.appendChild(main);
        li.appendChild(sub);
      } else {
        li.textContent = s.name;
      }
      li.addEventListener("click", () => {
        inputEl.value = s.value !== undefined ? s.value : s.name;
        hide();
      });
      listEl.appendChild(li);
    });
    if (items.length) {
      listEl.removeAttribute("hidden");
      listEl.setAttribute("aria-expanded", "true");
    } else {
      hide();
    }
  }

  function hide() {
    listEl.setAttribute("hidden", "");
    listEl.setAttribute("aria-expanded", "false");
  }

  inputEl.addEventListener("input", () => {
    clearTimeout(timer);
    const q = inputEl.value.trim();
    if (q.length < 2) return hide();
    timer = setTimeout(async () => {
      // Saisie de coordonnées GPS « lat,lon » : entrée unique locale,
      // pas d'appel serveur (le serveur sait déjà résoudre « lat,lon »).
      const mCoord = q.match(/^(-?\d{1,2}(?:\.\d+)?),\s*(-?\d{1,3}(?:\.\d+)?)$/);
      if (mCoord) {
        items = [{ name: `Position GPS : ${mCoord[1]}, ${mCoord[2]}`, value: `${mCoord[1]}, ${mCoord[2]}` }];
        render();
        return;
      }
      let data;
      try {
        const res = await fetch(`${API_BASE}/v1/stations/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ q, limit: 6 }),
        });
        if (!res.ok) return;
        data = await res.json();
      } catch {
        return;
      }
      items = [];
      for (const g of data.place_groups || []) {
        items.push({ name: `${g.name} — toutes les gares`, value: g.name });
      }
      if ((data.stations || []).length) {
        items.push({ header: "Gares" });
        items.push(...data.stations);
      }
      if ((data.communes || []).length) {
        items.push({ header: "Communes" });
        for (const c of data.communes) {
          items.push({
            name: c.name,
            value: c.id,
            sub: c.nearest_gare
              ? `gare la plus proche : ${c.nearest_gare} · ${String(c.nearest_km).replace(".", ",")} km`
              : undefined,
          });
        }
      }
      if ((data.bus_stops || []).length) {
        items.push({ header: "Arrêts de bus" });
        items.push(...data.bus_stops);
      }
      render();
    }, 200);
  });

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hide();
    if (e.key === "Tab" || e.key === "Enter") hide();
  });

  document.addEventListener("click", (e) => {
    if (!inputEl.contains(e.target) && !listEl.contains(e.target)) hide();
  });
}

setupAutocomplete(fromInput, $("#from-suggestions"));
setupAutocomplete(toInput, $("#to-suggestions"));

/* ------------------------------------------- rayon gares (double curseur) */
/* Échelle non linéaire 0 → 100 km : précision sur les petites distances,
   jusqu'au grand vélotage (75-100 km). [0,0] = « la plus proche » seule. */
const RADIUS_STOPS = [0, 1, 2, 5, 10, 15, 25, 50, 75, 100]; // km
const radiusMin = $("#radius-min");
const radiusMax = $("#radius-max");
const radiusLabel = $("#radius-label");
const radiusFill = $("#radius-fill");

function fmtKmFr(km) {
  return String(km).replace(".", ",");
}

function updateRadius() {
  let lo = parseInt(radiusMin.value, 10);
  let hi = parseInt(radiusMax.value, 10);
  if (lo > hi) [lo, hi] = [hi, lo];
  radiusMin.value = lo;
  radiusMax.value = hi;
  const minKm = RADIUS_STOPS[lo];
  const maxKm = RADIUS_STOPS[hi];
  const pct = (i) => (i / (RADIUS_STOPS.length - 1)) * 100;
  radiusFill.style.left = pct(lo) + "%";
  radiusFill.style.width = (pct(hi) - pct(lo)) + "%";
  if (maxKm === 0) {
    radiusLabel.textContent = "la plus proche uniquement";
  } else if (minKm === maxKm) {
    radiusLabel.textContent = `≈ ${fmtKmFr(minKm)} km`;
  } else {
    radiusLabel.textContent = `entre ${fmtKmFr(minKm)} et ${fmtKmFr(maxKm)} km`;
  }
}
radiusMin.addEventListener("input", updateRadius);
radiusMax.addEventListener("input", updateRadius);
updateRadius();

function radiusParams() {
  const a = RADIUS_STOPS[parseInt(radiusMin.value, 10)];
  const b = RADIUS_STOPS[parseInt(radiusMax.value, 10)];
  return { min: Math.min(a, b), max: Math.max(a, b) };
}

/* ------------------------------------------------------------ GPS 📍 */
document.querySelectorAll(".geo-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (!navigator.geolocation) return;
    const input = $("#" + btn.dataset.target);
    btn.disabled = true;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        btn.disabled = false;
        input.value = `${pos.coords.latitude.toFixed(5)}, ${pos.coords.longitude.toFixed(5)}`;
      },
      () => { btn.disabled = false; },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 }
    );
  });
});

/* T12 — cartes de réduction TER : menu multi-sélection alimenté par
   /v1/cards (regroupées par région). Les cartes cochées sont envoyées en
   `cards=id,id…` : l'API renvoie price_reduced_eur (meilleure carte par
   région) et pricing.cards (cartes réellement appliquées). */
const cardMenu = $("#cards-menu");
const cardToggle = $("#cards-toggle");
const cardPanel = $("#cards-panel");
const selectedCards = new Set();
let allCards = [];
let cardsByRegion = [];

function cardLabel(c) {
  if (c.discount_pct == null) return `${c.shortName || c.name} · abonnement/pass`;
  return `${c.shortName || c.name} (−${c.discount_pct} %)`;
}

function renderCardsPanel() {
  cardPanel.innerHTML = "";
  cardsByRegion.forEach(([region, list]) => {
    const group = document.createElement("li");
    group.className = "cards-group";
    const head = document.createElement("div");
    head.className = "cards-region";
    head.textContent = region;
    group.appendChild(head);
    list.forEach((c) => {
      const li = document.createElement("li");
      const label = document.createElement("label");
      label.className = "cards-item";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = c.id;
      cb.checked = selectedCards.has(c.id);
      cb.addEventListener("change", () => {
        if (cb.checked) selectedCards.add(c.id);
        else selectedCards.delete(c.id);
        updateCardsToggle();
      });
      const span = document.createElement("span");
      span.textContent = cardLabel(c);
      span.title = c.name;
      label.appendChild(cb);
      label.appendChild(span);
      li.appendChild(label);
      group.appendChild(li);
    });
    cardPanel.appendChild(group);
  });
  updateCardsToggle();
}

function updateCardsToggle() {
  if (selectedCards.size === 0) {
    cardToggle.textContent = "— sans carte de réduction —";
  } else if (selectedCards.size === 1) {
    const c = allCards.find((x) => x.id === [...selectedCards][0]);
    cardToggle.textContent = c ? cardLabel(c) : "1 carte sélectionnée";
  } else {
    cardToggle.textContent = `${selectedCards.size} cartes sélectionnées`;
  }
  cardToggle.classList.toggle("selected", selectedCards.size > 0);
}

function toggleCardsPanel(open) {
  cardPanel.toggleAttribute("hidden", !open);
  cardToggle.setAttribute("aria-expanded", String(open));
}

cardToggle.addEventListener("click", (e) => {
  e.preventDefault();
  toggleCardsPanel(cardPanel.hidden);
});

document.addEventListener("click", (e) => {
  if (!cardMenu.contains(e.target)) toggleCardsPanel(false);
});

fetch(API_BASE + "/v1/cards")
  .then((r) => (r.ok ? r.json() : Promise.reject()))
  .then((body) => {
    allCards = body.cards;
    const byRegion = new Map();
    allCards.forEach((c) => {
      const r = (c.region && c.region !== "INCONNUE") ? c.region : "Autre";
      if (!byRegion.has(r)) byRegion.set(r, []);
      byRegion.get(r).push(c);
    });
    cardsByRegion = [...byRegion.entries()]
      .sort((a, b) => a[0].localeCompare(b[0], "fr"))
      .map(([region, list]) => [region, [...list].sort((a, b) =>
        (a.shortName || a.name).localeCompare(b.shortName || b.name, "fr"))]);
    renderCardsPanel();
  })
  .catch(() => {});

/* ------------------------------------------------------------------ résultats */
function fmtTime(iso) {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function legBadge(leg) {
  const map = { train: ["Train TER", "badge-train"], car: ["Car TER", "badge-car"], bus: ["Bus", "badge-bus"],
    tram_train: ["TramTrain", "badge-tram"], walk: ["Marche", "badge-walk"] };
  const [label, cls] = map[leg.type] || [leg.type, "badge-walk"];
  return `<span class="badge ${cls}">${label}</span>`;
}

function nextDay(iso) {
  const [date] = iso.split("T");
  return date !== $("#date").value;
}

function delayBadge(leg) {
  if (!leg.delay_min) return "";
  const m = leg.delay_min;
  return `<span class="badge badge-delay" title="Retard réel">+${m} min</span>`;
}

function riskNote(j) {
  if (!j.connection_risks || !j.connection_risks.length) return "";
  const first = j.connection_risks[0];
  const n = j.connection_risks.length;
  return `<div class="connection-risk">⚠ Correspondance risquée à <strong>${first.at_station}</strong>` +
    ` (${first.from_line} +${first.delay_min} min → marge ${first.margin_min} min)` +
    `${n > 1 ? ` et ${n - 1} autre(s)` : ""}. Retard déjà consommé sur la marge planifiée.</div>`;
}

function escHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* Longueur d'affichage avant repli (« … ») d'une alerte. */
const ALERT_SHORT_LEN = 220;

function alertDesc(a) {
  const full = a.description || "";
  if (full.length <= ALERT_SHORT_LEN) {
    return `<p>${escHtml(full)}</p>`;
  }
  const cut = full.slice(0, ALERT_SHORT_LEN).replace(/\s+\S*$/, "");
  return `<p>${escHtml(cut)}…</p>` +
    `<span class="alert-more" role="button" tabindex="0" data-full="${escHtml(full)}">Voir plus</span>`;
}

function alertsNote(j) {
  if (!j.alerts || !j.alerts.length) return "";
  const head = j.alerts[0].header;
  const n = j.alerts.length;
  return `<div class="connection-risk alert-note">⚠ Perturbation signalée : <strong>${escHtml(head)}</strong>` +
    `${n > 1 ? ` (et ${n - 1} autre${n > 2 ? "s" : ""})` : ""}` +
    `<span class="alert-toggle" role="button" tabindex="0">Détail</span><div class="alert-details" hidden>` +
    j.alerts.map((a) => `<div class="alert-item"><strong>${escHtml(a.header)}</strong>${alertDesc(a)}</div>`).join("") +
    `</div></div>`;
}

let currentDetailJourney = null;

/* --------------------------------------------------- ANALYSE INTELLIGENTE DES PRIX */
function analyzePricing(j) {
  const normalPrice = j.price_normal_eur;
  const reducedPrice = j.price_reduced_eur;
  const hasCards = reducedPrice != null && reducedPrice < normalPrice;
  const cards = (j.pricing && j.pricing.cards) || [];

  // Découpage régional
  let split = null;
  const sp = j.pricing && j.pricing.split;
  if (sp) {
    const segsSum = sp.segments.reduce((acc, s) => acc + (s.fare_reduced_eur || s.fare_eur), 0);
    const singleTicket = sp.single_ticket_reduced_eur || sp.single_ticket_eur || normalPrice;
    const savings = singleTicket ? singleTicket - segsSum : 0;
    split = {
      available: true,
      profitable: savings > 0.5,
      savingsEur: savings,
      totalEur: segsSum,
      singleTicketEur: singleTicket,
      regions: sp.regions,
      junctions: sp.junction_stations,
      segments: sp.segments.map((s) => ({
        region: s.gap ? "Interrégional" : s.region,
        from: s.from.name,
        to: s.to.name,
        km: s.km,
        fareEur: s.fare_eur,
        fareReducedEur: s.fare_reduced_eur,
        finalPrice: s.fare_reduced_eur || s.fare_eur,
      }))
    };
  }

  // Meilleur prix global
  let bestPrice = reducedPrice || normalPrice || 0;
  if (split && split.profitable && split.totalEur < bestPrice) {
    bestPrice = split.totalEur;
  }

  const directPrice = sp
    ? (hasCards ? (sp.single_ticket_reduced_eur || sp.single_ticket_eur) : sp.single_ticket_eur)
    : (hasCards ? reducedPrice : normalPrice);
  const directNormalPrice = sp ? sp.single_ticket_eur : normalPrice;

  return {
    normalPrice,
    reducedPrice,
    directPrice: directPrice || normalPrice,
    directNormalPrice: directNormalPrice || normalPrice,
    hasCards,
    cards,
    bestPrice,
    split,
    rule: j.pricing ? (j.pricing.split ? "Découpage interrégional" : j.pricing.rule) : "Standard",
    legs: (j.pricing && j.pricing.legs) || []
  };
}

function fmtPrice(eur) {
  if (eur == null) return "—";
  return eur.toFixed(2).replace(".", ",") + " €";
}

function priceChip(j) {
  const p = analyzePricing(j);
  if (!p.normalPrice && !p.bestPrice) return "";

  let chips = [];
  if (p.hasCards) {
    chips.push(`<span class="price-chip" title="Tarif avec vos cartes"><s>${fmtPrice(p.directNormalPrice)}</s> <strong>${fmtPrice(p.bestPrice)}</strong></span>`);
  } else if (p.split && p.split.profitable) {
    chips.push(`<span class="price-chip price-chip-split" title="Découpage avantageux">✂️ Astuce ${fmtPrice(p.split.totalEur)} <span class="price-promo">−${fmtPrice(p.split.savingsEur)}</span></span>`);
  } else if (p.normalPrice) {
    chips.push(`<span class="price-chip" title="Plein tarif estimé">≈ ${fmtPrice(p.normalPrice)}</span>`);
  }

  return chips.join(" ");
}

/* --------------------------------------------------- LISTE DES RÉSULTATS */
function renderJourneys(journeys) {
  resultsSection.removeAttribute("hidden");
  journeysList.innerHTML = "";
  noResults.toggleAttribute("hidden", journeys.length > 0);

  journeys.forEach((j) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.className = "journey";
    btn.type = "button";
    const lines = [...new Set(j.legs.filter((l) => l.type !== "walk").map((l) => l.line))].join(" + ");
    const badges = j.legs.map(legBadge).join("");
    const delays = j.legs.map(delayBadge).join("");

    btn.innerHTML = `
      <div class="journey-head">
        <span class="journey-times">${fmtTime(j.departure)} → ${fmtTime(j.arrival)}</span>
        ${nextDay(j.arrival) ? '<span class="badge badge-next-day">+1j</span>' : ""}
        ${delays}
      </div>
      <div class="journey-meta">
        Durée ${Math.floor(j.duration_min / 60)}h${String(j.duration_min % 60).padStart(2, "0")}
        · ${j.transfers === 0 ? "Direct" : `${j.transfers} correspondance(s)`} · ${lines}${badges}
        <div style="margin-top: 0.35rem;">${priceChip(j)}</div>
      </div>
      ${(j.origin_note || j.destination_note) ? `<div class="connection-risk" style="background:#eff6ff; border-left-color:#3b82f6; color:#1e40af; margin-top:0.35rem;">📍 <strong>${escHtml([j.origin_note, j.destination_note].filter(Boolean).join(" · "))}</strong></div>` : ""}
      ${riskNote(j)}
      ${alertsNote(j)}
    `;
    btn.addEventListener("click", () => showDetail(j, true));
    li.appendChild(btn);
    journeysList.appendChild(li);
  });
}

/* --------------------------------------------------- TIMELINE ÉPURÉE */
function renderTimelineModern(j) {
  let html = `<ul class="timeline-modern">`;
  j.legs.forEach((leg, idx) => {
    const isWalk = leg.type === "walk";
    const isBus = leg.type === "bus";
    const modeIcon = isWalk ? "🚶" : isBus ? "🚌" : "🚆";
    const modeLabel = isWalk ? "Marche" : isBus ? "Bus" : "TER";
    const lineText = isWalk
      ? `Correspondance à pied (${leg.from.name} → ${leg.to.name})`
      : `${leg.line ? `${modeLabel} ${leg.line}` : modeLabel}${leg.line_name ? ` · ${leg.line_name}` : ""}`;
    const trainInfo = !isWalk
      ? `<div class="timeline-leg-details clickable" title="Cliquer pour voir tous les horaires de la ligne">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span class="timeline-train-badge">${modeIcon} ${lineText}</span>
            ${leg.vehicle_label ? `<small style="color:var(--muted);">${leg.vehicle_label}</small>` : ""}
          </div>
          ${leg.delay_min ? `<div class="rt-delay" style="font-size:0.8rem; margin-top:0.2rem;">⚠️ Retard estimé : ${leg.delay_min} min</div>` : ""}
          <div class="leg-schedule-hint">🕒 Voir tous les horaires de la ligne</div>
        </div>`
      : `<div class="timeline-leg-details" style="font-size:0.82rem; color:var(--muted);">${modeIcon} ${lineText}</div>`;

    // Gare de départ
    html += `
      <li class="timeline-item">
        <div class="timeline-dot station-dot"></div>
        <div class="timeline-header-row">
          <span class="timeline-time">${fmtTime(leg.from.time)}</span>
          <span class="timeline-station-name">${escHtml(leg.from.name)}</span>
          ${delayBadge(leg)}
        </div>
        ${trainInfo}
      </li>
    `;

    // Si c'est la dernière arrivée du trajet
    if (idx === j.legs.length - 1) {
      html += `
        <li class="timeline-item">
          <div class="timeline-dot station-dot" style="border-color:#10b981; background:#10b981;"></div>
          <div class="timeline-header-row">
            <span class="timeline-time">${fmtTime(leg.to.time)}</span>
            <span class="timeline-station-name">${escHtml(leg.to.name)}</span>
          </div>
        </li>
      `;
    } else {
      // Correspondance intermédiaire
      const nextLeg = j.legs[idx + 1];
      const waitMin = Math.round((new Date(nextLeg.from.time) - new Date(leg.to.time)) / 60000);
      html += `
        <li class="timeline-item">
          <div class="timeline-dot"></div>
          <div class="timeline-header-row">
            <span class="timeline-time">${fmtTime(leg.to.time)}</span>
            <span class="timeline-station-name">${escHtml(leg.to.name)}</span>
          </div>
          <div class="timeline-transfer-box">
            <span>⏳ Correspondance : <strong>${waitMin > 0 ? `${waitMin} min` : "Changement immédiat"}</strong> à ${escHtml(leg.to.name)}</span>
          </div>
        </li>
      `;
    }
  });
  html += `</ul>`;
  return html;
}

/* --------------------------------------------------- ACCORDÉON DÉTAILS TECHNIQUES */
function renderDetailsAccordion(j, p) {
  const legRows = p.legs.map((l) => `
    <div style="display:flex; justify-content:space-between; margin-bottom:0.25rem;">
      <span><strong>${l.line || "Ligne"}</strong> (${l.from} → ${l.to}) · ${l.km} km · ${l.region}</span>
      <span>${fmtPrice(l.fare_eur)}${l.fare_reduced_eur ? ` → ${fmtPrice(l.fare_reduced_eur)}` : ""}</span>
    </div>
  `).join("");

  return `
    <div class="details-accordion">
      <button type="button" class="accordion-toggle" onclick="this.nextElementSibling.toggleAttribute('hidden'); this.querySelector('.arr').textContent = this.nextElementSibling.hasAttribute('hidden') ? '▼' : '▲';">
        <span>🔍 Détails du calcul kilométrique & règles</span>
        <span class="arr">▼</span>
      </button>
      <div class="accordion-content" hidden>
        <div style="margin-bottom:0.5rem;"><strong>Règle appliquée :</strong> ${p.rule}</div>
        ${legRows}
        ${j.pricing && j.pricing.note ? `<div style="margin-top:0.4rem; font-style:italic;">${escHtml(j.pricing.note)}</div>` : ""}
      </div>
    </div>
  `;
}

/* --------------------------------------------------- RENDUS DES 3 STYLES */

// STYLE A : CARTES COMPARATIVES ÉPURÉES
function renderStyleA(j, p) {
  const showSplit = p.split && p.split.profitable;
  let cardsHtml = `<div class="pricing-cards-section">
    <div class="pricing-section-title">💳 Options tarifaires estimées</div>
    <div class="pricing-cards-grid">`;

  // Carte 1 : Billet Direct (Plein Tarif ou Tarif Réduit Carte)
  const isDirectCheapest = !showSplit;
  const directPrice = p.directPrice;
  cardsHtml += `
    <div class="pricing-card ${isDirectCheapest ? "featured" : ""}">
      <div>
        <div class="pricing-card-header">
          <span class="pricing-card-tag">${p.hasCards ? "Tarif Carte" : "Plein Tarif"}</span>
        </div>
        <div class="pricing-card-title">Billet Direct</div>
        <div class="pricing-card-desc">1 seul billet pour l'ensemble du voyage</div>
        <div class="pricing-card-price">
          ${fmtPrice(directPrice)}
          ${p.hasCards ? `<small><s>${fmtPrice(p.directNormalPrice)}</s></small>` : ""}
        </div>
      </div>
    </div>
  `;

  // Carte 2 : Astuce Découpage Malin (uniquement si rentable !)
  if (showSplit) {
    const sp = p.split;
    cardsHtml += `
      <div class="pricing-card featured">
        <div>
          <div class="pricing-card-header">
            <span class="pricing-card-tag">✂️ Découpage Malin</span>
            <span class="pricing-card-savings">Économie −${fmtPrice(sp.savingsEur)}</span>
          </div>
          <div class="pricing-card-title">${sp.segments.length} Billet${sp.segments.length > 1 ? "s" : ""} Découpé${sp.segments.length > 1 ? "s" : ""}</div>
          <div class="pricing-card-desc">${sp.junctions.length > 0 ? `Changement tarifaire à ${sp.junctions.join(" / ")}` : "Tarifs cumulés"}</div>
          <div class="pricing-card-price">
            ${fmtPrice(sp.totalEur)}
            ${sp.singleTicketEur ? `<small><s>${fmtPrice(sp.singleTicketEur)}</s></small>` : ""}
          </div>
          <ul class="pricing-card-steps">
            ${sp.segments.map((seg, i) => `
              <li>
                <span>${i + 1}. ${seg.from} → ${seg.to}</span>
                <strong>${fmtPrice(seg.finalPrice)}</strong>
              </li>
            `).join("")}
          </ul>
        </div>
      </div>
    `;
  }

  cardsHtml += `</div></div>`;

  return `
    ${cardsHtml}
    <div style="font-weight:700; margin-top:1.25rem; font-size:0.95rem; color:#1e293b;">Itinéraire & Correspondances</div>
    ${renderTimelineModern(j)}
    ${renderDetailsAccordion(j, p)}
  `;
}

/* --------------------------------------------------- VUE DÉTAIL DU TRAJET */
function showDetail(j, withAlternative) {
  currentDetailJourney = j;
  const p = analyzePricing(j);

  const alt = (withAlternative && j.connection_risks && j.connection_risks.length)
    ? `<button type="button" class="alt-button" id="alt-button">Voir une alternative plus tard (+30 min)</button>`
    : "";

  const styleContent = renderStyleA(j, p);

  detailBody.innerHTML = `
    <div class="journey-head">
      <span class="journey-times">${fmtTime(j.departure)} → ${fmtTime(j.arrival)}</span>
      ${nextDay(j.arrival) ? '<span class="badge badge-next-day">+1j</span>' : ""}
    </div>
    <p class="journey-meta">Durée ${Math.floor(j.duration_min / 60)}h${String(j.duration_min % 60).padStart(2, "0")}
      · ${j.transfers === 0 ? "Direct" : `${j.transfers} correspondance(s)`}</p>
    ${riskNote(j)}
    ${alertsNote(j)}
    ${alt}
    ${styleContent}
  `;

  const altBtn = detailBody.querySelector("#alt-button");
  if (altBtn) {
    altBtn.addEventListener("click", () => search(30));
  }

  // Horaires de ligne : écouteurs attachés directement (pas de onclick inline —
  // les noms de gares contenant une apostrophe cassaient la chaîne JS).
  const clickableLegs = detailBody.querySelectorAll(".timeline-leg-details.clickable");
  let ci = 0;
  j.legs.forEach((leg) => {
    if (leg.type === "walk") return;
    const el = clickableLegs[ci++];
    if (el && leg.trip_id) {
      el.addEventListener("click", () =>
        openScheduleModal(leg.trip_id, leg.from.name, leg.to.name));
    } else if (el) {
      el.classList.remove("clickable");
    }
  });

  resultsSection.setAttribute("hidden", "");
  detailSection.removeAttribute("hidden");
  detailSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("#detail-back").addEventListener("click", () => {
  currentDetailJourney = null;
  detailSection.setAttribute("hidden", "");
  resultsSection.removeAttribute("hidden");
});

/* -------------------------------------------------------------------- submit */
async function search(timeShiftMin = 0) {
  searchError.setAttribute("hidden", "");
  searchButton.disabled = true;
  searchButton.textContent = "Recherche…";

  let time = $("#time").value;
  if (timeShiftMin > 0) {
    const [hh, mm] = time.split(":").map(Number);
    const shifted = new Date(2000, 0, 1, hh, mm + timeShiftMin);
    time = `${String(shifted.getHours()).padStart(2, "0")}:${String(shifted.getMinutes()).padStart(2, "0")}`;
    $("#time").value = time;
  }

  const payload = {
    from: fromInput.value.trim(),
    to: toInput.value.trim(),
    date: $("#date").value,
    time: time,
    datetime_represents: form.elements.datetime_represents.value,
    sort: sortBy,
    count: 5,
  };

  if (prioritizeFewerTransfers) {
    payload.prioritize_fewer_transfers = true;
  }

  if (selectedCards.size) payload.cards = [...selectedCards].join(",");

  const includeBus = $("#include-bus");
  payload.vehicle = includeBus && includeBus.checked ? "bus_train" : "train_only";

  // Rayon gares (double curseur) — uniquement si ≠ défaut « la plus proche »
  const r = radiusParams();
  if (r.max > 0) {
    payload.radius_min_km = r.min;
    payload.radius_max_km = r.max;
  }

  try {
    let headers = {};
    try {
      headers = await PoW.headers();
    } catch (e) {
      // PoW échoue = pas de requête
    }
    headers["Content-Type"] = "application/json";
    const encrypted = await Crypto_.encrypt(payload);
    let res = await fetch(`${API_BASE}/v1/journeys`, {
      method: "POST",
      headers,
      body: JSON.stringify({ payload: encrypted }),
    });
    let body = await res.json();
    if (!res.ok && body.error && body.error.code === "POW_INVALID") {
      PoW.invalidate();
      try {
        headers = await PoW.headers();
      } catch (e) {}
      headers["Content-Type"] = "application/json";
      res = await fetch(`${API_BASE}/v1/journeys`, {
        method: "POST",
        headers,
        body: JSON.stringify({ payload: encrypted }),
      });
      body = await res.json();
    }
    if (!res.ok) {
      const err = body.error || {};
      searchError.textContent = err.message || "Erreur serveur.";
      if (err.suggestions && err.suggestions.length) {
        searchError.textContent += " Suggestion(s) : " + err.suggestions.join(", ");
      }
      searchError.removeAttribute("hidden");
      return;
    }
    lastJourneys = body.journeys;
    detailSection.setAttribute("hidden", "");
    renderJourneys(lastJourneys);
    setSortButtons();
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    searchError.textContent = "Impossible de contacter l'API.";
    searchError.removeAttribute("hidden");
  } finally {
    searchButton.disabled = false;
    searchButton.textContent = "Rechercher";
  }
}

let sortBy = "departure";
let prioritizeFewerTransfers = false;

function setSortButtons() {
  $("#sort-departure").classList.toggle("active", sortBy === "departure");
  $("#sort-duration").classList.toggle("active", sortBy === "duration");
}

// Tri local des résultats déjà affichés — aucune nouvelle requête.
function resortResults() {
  if (!Array.isArray(lastJourneys) || lastJourneys.length < 2 ||
      resultsSection.hasAttribute("hidden")) return;
  const durMin = (j) => j.duration_min != null
    ? j.duration_min
    : (new Date(j.arrival) - new Date(j.departure)) / 60000;
  if (sortBy === "duration") {
    lastJourneys.sort((a, b) => durMin(a) - durMin(b));
  } else {
    lastJourneys.sort((a, b) => String(a.departure).localeCompare(String(b.departure)));
  }
  renderJourneys(lastJourneys);
}

$("#sort-departure").addEventListener("click", () => {
  sortBy = "departure";
  setSortButtons();
  resortResults();
});

$("#sort-duration").addEventListener("click", () => {
  sortBy = "duration";
  setSortButtons();
  resortResults();
});

// Gestion de la case "Prioriser les moins de correspondances"
const prioritizeFewerTransfersCheckbox = $("#prioritize-fewer-transfers");
prioritizeFewerTransfersCheckbox.addEventListener("change", (e) => {
  prioritizeFewerTransfers = e.target.checked;
  if (prioritizeFewerTransfers) {
    sortBy = "transfers";
  } else {
    sortBy = "departure";
  }
  setSortButtons();
  if (!resultsSection.hasAttribute("hidden")) {
    search();
  }
});


form.addEventListener("submit", (e) => {
  e.preventDefault();
  search();
});

initDateRange();

/* ------------------------------------------------- PWA (T7 v2.1) */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js?v=17").then((reg) => {
      reg.addEventListener("updatefound", () => {
        const nw = reg.installing;
        if (nw) nw.addEventListener("statechange", () => {
          if (nw.state === "installed" && navigator.serviceWorker.controller) {
            window.dispatchEvent(new CustomEvent("sw-updated"));
          }
        });
      });
    }).catch(() => {});
  });
}

/* --------------------------------------------------- MODALE HORAIRES DE LIGNE */
const scheduleModal = $("#schedule-modal");
const modalClose = $("#modal-close");
const modalLineBadge = $("#modal-line-badge");
const modalLineTitle = $("#modal-line-title");
const modalLineDirection = $("#modal-line-direction");
const modalTabs = $("#modal-tabs");
const modalStopsList = $("#modal-stops-list");

let currentBoardName = "";
let currentAlightName = "";

if (modalClose) {
  modalClose.addEventListener("click", closeScheduleModal);
}
if (scheduleModal) {
  scheduleModal.addEventListener("click", (e) => {
    if (e.target === scheduleModal) closeScheduleModal();
  });
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && scheduleModal && !scheduleModal.hasAttribute("hidden")) {
    closeScheduleModal();
  }
});

function closeScheduleModal() {
  if (!scheduleModal) return;
  scheduleModal.setAttribute("hidden", "");
  scheduleModal.setAttribute("aria-hidden", "true");
}

async function openScheduleModal(tripId, boardName, alightName) {
  if (!tripId || !scheduleModal) return;
  currentBoardName = boardName || "";
  currentAlightName = alightName || "";

  const dateVal = $("#date").value;
  modalLineTitle.textContent = "Chargement des horaires...";
  modalLineBadge.textContent = "";
  modalLineDirection.textContent = "";
  modalTabs.innerHTML = "";
  modalStopsList.innerHTML = `<li style="padding:1.5rem; text-align:center; color:var(--muted); list-style:none;">Chargement de la grille horaire...</li>`;

  scheduleModal.removeAttribute("hidden");
  scheduleModal.setAttribute("aria-hidden", "false");

  try {
    const res = await fetch(`${API_BASE}/v1/trips/${encodeURIComponent(tripId)}/schedule?date=${encodeURIComponent(dateVal)}`);
    if (!res.ok) throw new Error("Erreur");
    const data = await res.json();
    renderScheduleModal(data, tripId);
  } catch (err) {
    modalLineTitle.textContent = "Horaires non disponibles";
    modalStopsList.innerHTML = `<li style="padding:1.5rem; text-align:center; color:#ef4444; list-style:none;">Impossible de charger la grille horaire de cette ligne.</li>`;
  }
}

function renderScheduleModal(data, selectedTripId) {
  const modeIcon = data.type === "bus" ? "🚌" : "🚆";
  const modeLabel = data.type === "bus" ? "Bus" : "TER";
  modalLineBadge.innerHTML = `${modeIcon} ${modeLabel} ${escHtml(data.line || "")}`;
  modalLineTitle.textContent = data.line_name || `Ligne ${data.line}`;
  modalLineDirection.textContent = data.direction_name || "";

  modalTabs.innerHTML = "";
  let activeTrip = (data.trips || []).find((t) => t.trip_id === selectedTripId) || (data.trips && data.trips[0]);

  (data.trips || []).forEach((t) => {
    const isCurrent = t.trip_id === data.current_trip_id;
    const isSelected = t.trip_id === (activeTrip && activeTrip.trip_id);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `modal-tab-btn ${isSelected ? "active" : ""}`;
    btn.innerHTML = `
      <span>${t.departure_time}</span>
      ${isCurrent ? '<span class="current-star" title="Votre trajet actuel">★</span>' : ""}
    `;
    btn.addEventListener("click", () => {
      modalTabs.querySelectorAll(".modal-tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderScheduleTripStops(t);
    });
    modalTabs.appendChild(btn);

    if (isSelected) {
      setTimeout(() => btn.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" }), 60);
    }
  });

  if (activeTrip) {
    renderScheduleTripStops(activeTrip);
  } else {
    modalStopsList.innerHTML = `<li style="padding:1.5rem; text-align:center; color:var(--muted); list-style:none;">Aucune autre circulation trouvée pour cette date.</li>`;
  }
}

function renderScheduleTripStops(trip) {
  let html = "";
  trip.stops.forEach((st, idx) => {
    const isFirst = idx === 0;
    const isLast = idx === trip.stops.length - 1;
    const isUserBoard = currentBoardName && st.name.toLowerCase().includes(currentBoardName.toLowerCase());
    const isUserAlight = currentAlightName && st.name.toLowerCase().includes(currentAlightName.toLowerCase());
    const isHighlighted = isUserBoard || isUserAlight;

    const timeStr = isFirst ? st.departure_time : isLast ? st.arrival_time : (st.departure_time || st.arrival_time);

    html += `
      <li class="modal-stop-item ${isHighlighted ? "highlight" : ""}">
        <div class="modal-stop-dot"></div>
        <span class="modal-stop-name">
          ${escHtml(st.name)}
          ${isUserBoard ? '<small style="color:var(--ter); font-weight:700; display:block;">▲ Votre gare de montée</small>' : ""}
          ${isUserAlight ? '<small style="color:var(--ter); font-weight:700; display:block;">▼ Votre gare de descente</small>' : ""}
        </span>
        <span class="modal-stop-time">${timeStr}</span>
      </li>
    `;
  });
  modalStopsList.innerHTML = html;
}

/* ------------------------------------------------------------- événements (délégués) */
document.addEventListener("click", (ev) => {
  const t = ev.target.closest(".alert-toggle");
  if (!t) return;
  ev.stopPropagation();
  const box = t.nextElementSibling;
  box.toggleAttribute("hidden");
  t.textContent = box.hidden ? "Détail" : "Masquer";
});

/* T13 — « Voir plus » : remplace le texte tronqué (… ) par le texte complet
   de l'alerte, puis retire le bouton. */
document.addEventListener("click", (ev) => {
  const m = ev.target.closest(".alert-more");
  if (!m) return;
  ev.stopPropagation();
  const p = m.previousElementSibling;
  if (p) p.textContent = m.dataset.full;
  m.remove();
});
