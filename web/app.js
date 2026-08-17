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

  return { headers };
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
      li.textContent = s.name;
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
      const res = await fetch(`${API_BASE}/v1/stations/search?q=${encodeURIComponent(q)}&limit=6`);
      if (!res.ok) return;
      const data = await res.json();
      const groups = (data.place_groups || []).map((g) => ({
        name: `${g.name} — toutes les gares`,
        value: g.name,
      }));
      items = [...groups, ...(data.stations || [])];
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
  const map = { train: ["Train TER", "badge-train"], car: ["Car TER", "badge-car"],
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
    const isTrain = leg.type !== "walk";
    const trainInfo = isTrain
      ? `<div class="timeline-leg-details">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span class="timeline-train-badge">🚆 ${leg.line ? `TER ${leg.line}` : "TER"}${leg.line_name ? ` · ${leg.line_name}` : ""}</span>
            ${leg.vehicle_label ? `<small style="color:var(--muted);">${leg.vehicle_label}</small>` : ""}
          </div>
          ${leg.delay_min ? `<div class="rt-delay" style="font-size:0.8rem; margin-top:0.2rem;">⚠️ Retard estimé : ${leg.delay_min} min</div>` : ""}
        </div>`
      : `<div class="timeline-leg-details" style="font-size:0.82rem; color:var(--muted);">🚶 Correspondance à pied (${leg.from.name} → ${leg.to.name})</div>`;

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
  let cardsHtml = `<div class="pricing-cards-section">
    <div class="pricing-section-title">💳 Options tarifaires estimées</div>
    <div class="pricing-cards-grid">`;

  // Carte 1 : Billet Direct (Plein Tarif ou Tarif Réduit Carte)
  const isDirectCheapest = !p.split || !p.split.profitable;
  const directPrice = p.directPrice;
  cardsHtml += `
    <div class="pricing-card ${isDirectCheapest ? "featured" : ""}">
      <div>
        <div class="pricing-card-header">
          <span class="pricing-card-tag">${p.hasCards ? "Tarif Carte" : "Plein Tarif"}</span>
          ${isDirectCheapest ? '<span class="pricing-card-savings">Recommandé</span>' : ""}
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

  // Carte 2 : Astuce Découpage Malin (si disponible)
  if (p.split) {
    const sp = p.split;
    cardsHtml += `
      <div class="pricing-card ${sp.profitable ? "featured" : ""}">
        <div>
          <div class="pricing-card-header">
            <span class="pricing-card-tag">✂️ Découpage Malin</span>
            ${sp.profitable ? `<span class="pricing-card-savings">Économie −${fmtPrice(sp.savingsEur)}</span>` : ""}
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

  const params = new URLSearchParams({
    from: fromInput.value.trim(),
    to: toInput.value.trim(),
    date: $("#date").value,
    time: time,
    datetime_represents: form.elements.datetime_represents.value,
    sort: sortBy,
    count: "5",
  });
  
  // Ajouter le paramètre pour la priorisation des correspondances
  if (prioritizeFewerTransfers) {
    params.set("prioritize_fewer_transfers", "true");
  }

  if (selectedCards.size) params.set("cards", [...selectedCards].join(","));

  try {
    let headers = {};
    try {
      headers = await PoW.headers();
    } catch (e) {
      // PoW échoue = pas de requête
    }
    const res = await fetch(`${API_BASE}/v1/journeys?${params.toString()}`, { headers });
    const body = await res.json();
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

$("#sort-departure").addEventListener("click", () => {
  sortBy = "departure";
  search();
});

$("#sort-duration").addEventListener("click", () => {
  sortBy = "duration";
  search();
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
  // Relancer la recherche uniquement si la section des résultats est visible
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
    navigator.serviceWorker.register("/sw.js").then((reg) => {
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
