/* planTER — SPA statique : recherche, résultats, détail (§8.2). */
"use strict";

const $ = (sel) => document.querySelector(sel);

/* T7 v2.2 — en web, l''API est servie à la même origine (''/v1/*'') ;
   dans le shell natif Capacitor (WebView), on appelle l''API publique. */
const IS_CAPACITOR = typeof window.Capacitor !== "undefined"
  && typeof window.Capacitor.isNativePlatform === "function"
  && window.Capacitor.isNativePlatform();
const API_BASE = IS_CAPACITOR ? "https://ter.zvz.fr" : "";

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
        inputEl.value = s.name;
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
      items = (await res.json()).stations;
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

function alertsNote(j) {
  if (!j.alerts || !j.alerts.length) return "";
  const head = j.alerts[0].header;
  const n = j.alerts.length;
  return `<div class="connection-risk alert-note">⚠ Perturbation signalée : <strong>${head}</strong>` +
    `${n > 1 ? ` (et ${n - 1} autre${n > 2 ? "s" : ""})` : ""}` +
    `<span class="alert-toggle" role="button" tabindex="0">Détail</span><div class="alert-details" hidden>` +
    j.alerts.map((a) => `<div class="alert-item"><strong>${a.header}</strong><p>${a.description}</p></div>`).join("") +
    `</div></div>`;
}

function renderJourneys(journeys) {
  resultsSection.removeAttribute("hidden");
  journeysList.innerHTML = "";
  noResults.toggleAttribute("hidden", journeys.length > 0);

  journeys.forEach((j, idx) => {
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
        · ${j.transfers} correspondance(s) · ${lines}${badges}
        ${priceChip(j)}
      </div>
      ${riskNote(j)}
      ${alertsNote(j)}
      ${ticketLinks(j)}
    `;
    btn.addEventListener("click", () => showDetail(j, true));
    li.appendChild(btn);
    journeysList.appendChild(li);
  });
}

/* T12 — prix estimé (modèle v1) : affiché comme une estimation, jamais comme
   un tarif officiel. */
function fmtPrice(eur) {
  return eur.toFixed(2).replace(".", ",") + " €";
}

function priceChip(j) {
  if (j.price_normal_eur == null) return "";
  const title = (j.pricing && j.pricing.note) || "prix estimé";
  if (j.price_reduced_eur != null && j.price_reduced_eur < j.price_normal_eur) {
    return ` <span class="price-chip" title="${title}">≈ <s>${fmtPrice(j.price_normal_eur)}</s> ${fmtPrice(j.price_reduced_eur)}</span>`;
  }
  return ` <span class="price-chip" title="${title}">≈ ${fmtPrice(j.price_normal_eur)}</span>`;
}

function priceBlock(j) {
  if (j.price_normal_eur == null || !j.pricing) return "";
  const rows = j.pricing.legs
    .map((l) => `<div class="price-leg"><span class="price-leg-line">${l.line}</span>
      <span>${l.km} km</span><span class="price-leg-region">${l.region}</span></div>`)
    .join("");
  const rule = j.pricing.split
    ? "billets découpés par région, sommé"
    : j.pricing.rule === "mono_region"
      ? "un seul billet dégressif sur la distance totale"
      : "un billet par tronçon, sommé";
  const cards = (j.pricing.cards || [])
    .map((c) => `<span class="price-card">${c.shortName} (−${Math.round((1 - c.pay) * 100)} %)</span>`)
    .join("");
  const reduced = (j.price_reduced_eur != null && j.price_reduced_eur < j.price_normal_eur)
    ? `<div class="price-reduced">Tarif réduit : ≈ <strong>${fmtPrice(j.price_reduced_eur)}</strong> ${cards}</div>`
    : "";
  /* §32 — un même train traversant plusieurs régions : on annonce la coupure
     (billet par segment régional) et le prix découpé, avec le lien de
     réservation par segment (la carte de la région du segment y est ajoutée). */
  let splitBlock = "";
  const sp = j.pricing.split;
  if (sp) {
    const gap = sp.segments.find((s) => s.gap);
    const segs = sp.segments
      .map((s) => `<div class="price-leg split-seg"><span class="price-leg-line">${s.gap ? "Interrégional (plein tarif)" : s.region}</span>
        <span>${s.from.name} → ${s.to.name} · ${s.km} km</span>
        <span class="price-leg-region">${fmtPrice(s.fare_eur)}${s.fare_reduced_eur < s.fare_eur ? ` → ${fmtPrice(s.fare_reduced_eur)}` : ""}</span>
        ${s.booking && s.booking.url ? ` <a class="ticket-chip" href="${s.booking.url}" target="_blank" rel="noopener noreferrer">Réserver</a>` : ""}</div>`)
      .join("");
    const announce = gap
      ? `Ce train traverse ${sp.regions.join(" et ")} : pas d'accord tarifaire entre les régions, il faut un plein tarif entre ${gap.from.name} et ${gap.to.name} puis la carte de la région suivante.`
      : `Ce train traverse ${sp.regions.join(" et ")} : pour utiliser la carte de chaque région, découpez le billet à ${sp.junction_stations.join(" / ")}.`;
    splitBlock = `<div class="price-detail split-note">
      <div class="price-rule">${announce}</div>
      ${segs}
      ${sp.single_ticket_eur != null ? `<div class="price-rule">Billet unique (si vendu en une fois) : ≈ ${fmtPrice(sp.single_ticket_eur)}${sp.single_ticket_reduced_eur < sp.single_ticket_eur ? ` → ${fmtPrice(sp.single_ticket_reduced_eur)}` : ""}</div>` : ""}
      <div class="price-split-total">Total billets découpés : ≈ <strong>${fmtPrice(sp.price_reduced_split_eur)}</strong> (plein tarif ${fmtPrice(sp.price_split_eur)})</div>
    </div>`;
  }
  return `<div class="price-note">
    <strong>Prix estimé : ≈ ${fmtPrice(j.price_normal_eur)}</strong>
    ${reduced}
    <div class="price-detail">${rows}<div class="price-rule">Règle : ${rule}.</div>
    <small>${j.pricing.note}.</small></div>
    ${splitBlock}
  </div>`;
}

/* T11 — lien Trainline « Réserver le trajet » (total) + billets par leg.
   La logique des cartes (backend, param cards=) est conservée pour plus tard. */
function ticketLinks(j) {
  if (!j.booking || !j.booking.tickets) return "";
  const total = j.booking.total_url
    ? `<a class="ticket-chip ticket-total" href="${j.booking.total_url}" target="_blank" rel="noopener noreferrer">Réserver le trajet (Trainline)</a>`
    : "";
  const legs = j.legs.filter((l) => l.booking && l.booking.url)
    .map((l) => `<a class="ticket-chip" href="${l.booking.url}" target="_blank" rel="noopener noreferrer">Billet ${l.line || "trajet"} · Trainline</a>`)
    .join("");
  return `<div class="ticket-links">${total}${legs}</div>`;
}

/* ------------------------------------------------------------------- détail */
function showDetail(j, withAlternative) {
  const alt = (withAlternative && j.connection_risks && j.connection_risks.length)
    ? `<button type="button" class="alt-button" id="alt-button">Voir une alternative plus tard (+30 min)</button>`
    : "";
  detailBody.innerHTML = `<div class="journey-head">
      <span class="journey-times">${fmtTime(j.departure)} → ${fmtTime(j.arrival)}</span>
      ${nextDay(j.arrival) ? '<span class="badge badge-next-day">+1j</span>' : ""}
      ${priceChip(j)}
    </div>
    <p class="journey-meta">Durée ${Math.floor(j.duration_min / 60)}h${String(j.duration_min % 60).padStart(2, "0")}
      · ${j.transfers} correspondance(s)</p>
    ${riskNote(j)}
    ${alertsNote(j)}
    ${priceBlock(j)}
    ${alt}
    <ol class="timeline"></ol>`;
  const timeline = detailBody.querySelector(".timeline");

  j.legs.forEach((leg) => {
    const li = document.createElement("li");
    const info = leg.type === "walk"
      ? `Marche ${leg.from.name} → ${leg.to.name}`
      : `${legBadge(leg)} Ligne ${leg.line}${leg.line_name ? " — " + leg.line_name : ""}` +
        (leg.vehicle_label ? ` · ${leg.vehicle_label}` : "") +
        (leg.delay_min ? ` · <strong class="rt-delay">retard ${leg.delay_min} min</strong>` : "");
    const buy = (leg.booking && leg.booking.url)
      ? ` <a class="ticket-chip" href="${leg.booking.url}" target="_blank" rel="noopener noreferrer">Acheter ce billet (Trainline)</a>`
      : "";
    li.innerHTML = `<div class="time">${fmtTime(leg.from.time)}${delayBadge(leg)}</div>
      <div><span class="station">${leg.from.name}</span><div class="leg-info">${info}${buy}</div></div>`;
    timeline.appendChild(li);
    const li2 = document.createElement("li");
    li2.innerHTML = `<div class="time">${fmtTime(leg.to.time)}</div>
      <div><span class="station">${leg.to.name}</span></div>`;
    timeline.appendChild(li2);
  });

  const altBtn = detailBody.querySelector("#alt-button");
  if (altBtn) {
    altBtn.addEventListener("click", () => search(30));
  }

  resultsSection.setAttribute("hidden", "");
  detailSection.removeAttribute("hidden");
  detailSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("#detail-back").addEventListener("click", () => {
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
    count: sortBy === "duration" ? "10" : "5",
  });
  if (selectedCards.size) params.set("cards", [...selectedCards].join(","));

  try {
    const res = await fetch(`${API_BASE}/v1/journeys?${params.toString()}`);
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
