/* TER Finder — SPA statique : recherche, résultats, détail (§8.2). */
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
      d.value = h.coverage_start;
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
    btn.innerHTML = `
      <div class="journey-head">
        <span class="journey-times">${fmtTime(j.departure)} → ${fmtTime(j.arrival)}</span>
        ${nextDay(j.arrival) ? '<span class="badge badge-next-day">+1j</span>' : ""}
      </div>
      <div class="journey-meta">
        Durée ${Math.floor(j.duration_min / 60)}h${String(j.duration_min % 60).padStart(2, "0")}
        · ${j.transfers} correspondance(s) · ${lines}${badges}
      </div>
      ${j.booking && j.booking.tickets
        ? `<div class="ticket-links">${j.legs.filter((l) => l.booking && l.booking.url)
            .map((l) => `<a class="ticket-chip" href="${l.booking.url}" target="_blank" rel="noopener noreferrer">Billet ${l.line || "trajet"} · Trainline</a>`)
            .join("")}</div>`
        : ""}`;
    btn.addEventListener("click", () => showDetail(j));
    li.appendChild(btn);
    journeysList.appendChild(li);
  });
}

/* ------------------------------------------------------------------- détail */
function showDetail(j) {
  detailBody.innerHTML = `<div class="journey-head">
      <span class="journey-times">${fmtTime(j.departure)} → ${fmtTime(j.arrival)}</span>
      ${nextDay(j.arrival) ? '<span class="badge badge-next-day">+1j</span>' : ""}
    </div>
    <p class="journey-meta">Durée ${Math.floor(j.duration_min / 60)}h${String(j.duration_min % 60).padStart(2, "0")}
      · ${j.transfers} correspondance(s)</p>
    <ol class="timeline"></ol>`;
  const timeline = detailBody.querySelector(".timeline");

  j.legs.forEach((leg) => {
    const li = document.createElement("li");
    const info = leg.type === "walk"
      ? `Marche ${leg.from.name} → ${leg.to.name}`
      : `${legBadge(leg)} Ligne ${leg.line}${leg.line_name ? " — " + leg.line_name : ""}` +
        (leg.vehicle_label ? ` · ${leg.vehicle_label}` : "");
    const buy = (leg.booking && leg.booking.url)
      ? ` <a class="ticket-chip" href="${leg.booking.url}" target="_blank" rel="noopener noreferrer">Acheter ce billet (Trainline)</a>`
      : "";
    li.innerHTML = `<div class="time">${fmtTime(leg.from.time)}</div>
      <div><span class="station">${leg.from.name}</span><div class="leg-info">${info}${buy}</div></div>`;
    timeline.appendChild(li);
    const li2 = document.createElement("li");
    li2.innerHTML = `<div class="time">${fmtTime(leg.to.time)}</div>
      <div><span class="station">${leg.to.name}</span></div>`;
    timeline.appendChild(li2);
  });

  resultsSection.setAttribute("hidden", "");
  detailSection.removeAttribute("hidden");
  detailSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("#detail-back").addEventListener("click", () => {
  detailSection.setAttribute("hidden", "");
  resultsSection.removeAttribute("hidden");
});

/* -------------------------------------------------------------------- submit */
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  searchError.setAttribute("hidden", "");
  searchButton.disabled = true;
  searchButton.textContent = "Recherche…";

  const params = new URLSearchParams({
    from: fromInput.value.trim(),
    to: toInput.value.trim(),
    date: $("#date").value,
    time: $("#time").value,
    datetime_represents: form.elements.datetime_represents.value,
    max_transfers: $("#max_transfers").value,
    vehicle: form.elements.vehicle.checked ? "train_only" : "all",
    count: "5",
  });

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
    renderJourneys(lastJourneys);
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    searchError.textContent = "Impossible de contacter l'API.";
    searchError.removeAttribute("hidden");
  } finally {
    searchButton.disabled = false;
    searchButton.textContent = "Rechercher";
  }
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
