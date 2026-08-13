"""T12 — pricing.py : estimation tarifaire TER (MVP).

Modèle (cf. config/pricing.yaml) :
- distance ferroviaire estimée d'un leg = somme des haversine entre les
  arrêts consécutifs du train × `rail_factor` ;
- région d'un train = région majoritaire de ses arrêts (config/station_regions) ;
- prix d'un billet = scale_region × (a·√km + b·km), arrondi aux 5 centimes,
  plancher min_eur ;
- agrégation : trajet mono-région -> un billet sur la distance totale cumulée
  (dégressivité globale) ; pluri-région -> somme des billets par tronçon ;
- cartes de réduction (T12) : chaque carte a une fraction `pay` du plein tarif
  (0.50 = -50 %) et une région d'application (déduite de son nom). La réduction
  ne s'applique QUE sur les segments de la région de la carte ; le tarif réduit
  retenu est la meilleure réduction parmi les cartes demandées.

Les prix sont des ESTIMATIONS : le modèle est calibré sur quelques prix
observés (Trainline, 12/08/2026) et les barèmes régionaux réels n'y sont pas
tous disponibles. L'API marque ces prix comme estimés.
"""

from __future__ import annotations

import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import TYPE_CHECKING

import yaml  # noqa: E402

from src import trainline_cards

if TYPE_CHECKING:
    from src.graph import Graph

ROOT = Path(__file__).resolve().parents[1]
PRICING_FILE = ROOT / "config" / "pricing.yaml"
REGIONS_FILE = ROOT / "config" / "station_regions.json"

EARTH_R_KM = 6371.0088
_STOPAREA = "StopArea:"
_OCE = "OCE"


# -------------------------------------------------------------- distances
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance à vol d'oiseau (km) entre deux points."""
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * EARTH_R_KM * asin(sqrt(a))


class PricingEngine:
    """Tarification d'un trajet à partir du graphe et des configs.

    Caches lazy (à l'exécution d'un même process) : distance par segment de
    trip et région par trip. Les accès concurrents sont bénins (recalculs
    redondants, valeurs identiques).
    """

    def __init__(self, graph: "Graph", pricing: Path = PRICING_FILE, regions: Path = REGIONS_FILE):
        self.graph = graph
        cfg = yaml.safe_load(pricing.read_text(encoding="utf-8"))
        self.rail_factor = float(cfg["rail_factor"])
        self.min_eur = float(cfg["min_eur"])
        self.round_to = float(cfg["round_to"])
        self.a = float(cfg["a"])
        self.b = float(cfg["b"])
        self.default_scale = float(cfg["default_scale"])
        self.region_scale = {name: float(v["scale"]) for name, v in cfg.get("regions", {}).items()}
        self._stations = json.loads(regions.read_text(encoding="utf-8"))
        self._stop_region: dict[int, str] = {}
        self._trip_region: dict[int, str] = {}
        self._leg_km: dict[tuple[int, int, int], float] = {}
        self._card_info: dict[str, dict] = {}
        self._load_cards(cfg.get("cards", {}))

    # --------------------------------------------------- règles des cartes
    def _load_cards(self, ccfg: dict) -> None:
        self.card_default_pay = float(ccfg.get("default_pay", 0.50))
        self._no_discount_prefixes = tuple(str(p).lower() for p in ccfg.get("no_discount_prefixes", []))
        self._pay_patterns = [(str(p["contains"]).lower(), float(p["pay"])) for p in ccfg.get("pay_patterns", [])]
        self._card_by_id = ccfg.get("by_id", {})
        self._region_keywords = [(str(k["contains"]).lower(), k["region"]) for k in ccfg.get("region_keywords", [])]

    def card_info(self, card: dict) -> dict:
        """pay (fraction du plein tarif PAYÉE, None si aucune réduction par
        billet) et région d'application d'une carte TER (config/trainline_cards)."""
        cid = card["id"]
        if cid not in self._card_info:
            name = card.get("name", "")
            low = name.lower()
            pay: float | None = None
            if not any(low.startswith(pref) for pref in self._no_discount_prefixes):
                ov = self._card_by_id.get(cid)
                if ov is not None:
                    pay = None if ov.get("type") == "none" else float(ov.get("pay", self.card_default_pay))
                else:
                    for contains, p in self._pay_patterns:
                        if contains in low:
                            pay = p
                            break
                    if pay is None:
                        pay = self.card_default_pay
            region = "INCONNUE"
            for contains, r in self._region_keywords:
                if contains in low:
                    region = r
                    break
            self._card_info[cid] = {"pay": pay, "region": region}
        return self._card_info[cid]

    @staticmethod
    def _discount(eur: float, pay: float) -> float:
        """Prix réduit d'un billet plein tarif (arrondi aux 5 centimes)."""
        return round(round(eur * pay / 0.05) * 0.05, 2)

    # ------------------------------------------------------ région d'un arrêt
    def _uic(self, stop_idx: int) -> str | None:
        stop_id = self.graph.stops[stop_idx].id
        if stop_id.startswith(_STOPAREA):
            stop_id = stop_id[len(_STOPAREA):]
        if not stop_id.startswith(_OCE):
            return None
        return stop_id[len(_OCE):]

    def stop_region(self, stop_idx: int) -> str:
        if stop_idx not in self._stop_region:
            uic = self._uic(stop_idx)
            region = self._stations.get(uic or "", {}).get("region") if uic else None
            self._stop_region[stop_idx] = region or "INCONNUE"
        return self._stop_region[stop_idx]

    # ------------------------------------------------------ région d'un train
    def trip_region(self, trip_idx: int) -> str:
        if trip_idx not in self._trip_region:
            counts: dict[str, int] = {}
            seen: set[int] = set()
            for st in self.graph.trips[trip_idx].stop_times:
                if st.stop in seen:
                    continue
                seen.add(st.stop)
                r = self.stop_region(st.stop)
                counts[r] = counts.get(r, 0) + 1
            # majorité (à égalité : le premier par ordre d'apparition)
            self._trip_region[trip_idx] = max(counts.items(), key=lambda kv: kv[1])[0]
        return self._trip_region[trip_idx]

    # ------------------------------------------------------ distance d'un leg
    def leg_km(self, trip_idx: int, board_stop: int, alight_stop: int) -> float:
        key = (trip_idx, board_stop, alight_stop)
        if key not in self._leg_km:
            stops = self.graph.trips[trip_idx].stop_times
            bi = ai = -1
            for i, st in enumerate(stops):
                if st.stop == board_stop and bi < 0:
                    bi = i
                if st.stop == alight_stop:
                    ai = i
            km = 0.0
            if 0 <= bi < ai:
                for k in range(bi, ai):
                    s1, s2 = self.graph.stops[stops[k].stop], self.graph.stops[stops[k + 1].stop]
                    km += haversine_km(s1.lat, s1.lon, s2.lat, s2.lon)
            self._leg_km[key] = km * self.rail_factor
        return self._leg_km[key]

    # -------------------------------------- segments régionaux intra-train
    def _segment(self, trip_idx: int, i0: int, i1: int, region: str) -> dict:
        """Segment régional [positions i0..i1] d'un trip : gares extrêmes,
        distance (haversine × rail_factor) et horaires (minutes minuit)."""
        stops = self.graph.trips[trip_idx].stop_times
        gs = self.graph.stops
        km = 0.0
        for k in range(i0, i1):
            s1, s2 = gs[stops[k].stop], gs[stops[k + 1].stop]
            km += haversine_km(s1.lat, s1.lon, s2.lat, s2.lon)
        return {
            "region": region,
            "from": {"stop_area_id": gs[stops[i0].stop].id, "name": gs[stops[i0].stop].name},
            "to": {"stop_area_id": gs[stops[i1].stop].id, "name": gs[stops[i1].stop].name},
            "km": round(km * self.rail_factor, 1),
            "departure_min": stops[i0].dep,
            "arrival_min": stops[i1].arr,
        }

    def trip_region_segments(self, trip_idx: int, board: int, alight: int) -> list[dict]:
        """Découpage de la portion montée->descente d'un train en segments
        régionaux consécutifs (T12 §32).

        On groupe les arrêts consécutifs de même région le long des
        `stop_times` ; à chaque bascule de région, la gare de jonction est le
        dernier arrêt du segment sortant. Les arrêts de région inconnue sont
        absorbés par le segment courant (pas de fausse coupure)."""
        stops = self.graph.trips[trip_idx].stop_times
        bi = ai = -1
        for i, st in enumerate(stops):
            if st.stop == board and bi < 0:
                bi = i
            if st.stop == alight:
                ai = i
        if not (0 <= bi < ai):
            return [self._segment(trip_idx, 0, len(stops) - 1, self.trip_region(trip_idx))]
        segments: list[dict] = []
        cur = self.stop_region(stops[bi].stop)
        begin = bi
        for i in range(bi + 1, ai + 1):
            region = self.stop_region(stops[i].stop)
            if region != cur and region != "INCONNUE" and cur != "INCONNUE":
                segments.append(self._segment(trip_idx, begin, i - 1, cur))
                cur = region
                # le segment suivant démarre à la gare de jonction (dernier
                # arrêt de la région sortante) : les billets sont contigus et
                # chacun démarre là où on découpe (ex. Mâcon).
                begin = i - 1
        segments.append(self._segment(trip_idx, begin, ai, cur))
        # Nettoyage (§32) : les segments de distance nulle (région limitrophe
        # tenant sur un seul arrêt, ex. Île-de-France à Paris Gare de Lyon) ne
        # font pas un billet à part. Ils sont absorbés dans le segment voisin
        # (pour les tout premiers, on remonte la gare de montée) ; les segments
        # consécutifs de même région sont ensuite fusionnés.
        useful = [i for i, seg in enumerate(segments) if seg["km"] > 0.0]
        if not useful:
            return segments
        out: list[dict] = []
        for idx, seg in enumerate(segments):
            if seg["km"] > 0.0:
                if out and out[-1]["region"] == seg["region"]:
                    out[-1]["to"] = seg["to"]
                    out[-1]["arrival_min"] = seg["arrival_min"]
                    out[-1]["km"] = round(out[-1]["km"] + seg["km"], 1)
                else:
                    out.append(dict(seg))
            elif out:
                out[-1]["to"] = seg["to"]
                out[-1]["arrival_min"] = seg["arrival_min"]
        for i in range(useful[0]):
            out[0]["from"] = segments[i]["from"]
            out[0]["departure_min"] = segments[i]["departure_min"]
        return out

    # ---------------------------------------------------------------- tarif
    def fare(self, km: float, region: str) -> float:
        """Prix estimé d'un billet sur `km` km dans `region`."""
        scale = self.region_scale.get(region, self.default_scale)
        raw = scale * (self.a * sqrt(km) + self.b * km) if km > 0 else 0.0
        return max(self.min_eur, round(round(raw / self.round_to) * self.round_to, 2))

    # ------------------------------------------------------------- trajet
    def journey_price(self, journey, cards: list[str] | None = None) -> dict | None:
        """Prix estimé d'un trajet (legs ferroviaires uniquement).

        `cards` : ids Trainline (config/trainline_cards.json) des cartes de
        réduction à appliquer. La réduction d'une carte ne vaut que pour les
        segments de sa région ; parmi plusieurs cartes, la plus avantageuse
        s'applique. Retourne None si le trajet n'a aucun leg ferroviaire.
        """
        rail = [l for l in journey.legs if l.type != "walk"]
        if not rail:
            return None

        legs: list[dict] = []
        regions: set[str] = set()
        card_regions: set[str] = set()
        total_km = 0.0
        for leg in rail:
            trip_idx = self.graph.trip_index.get(leg.trip_id)
            if trip_idx is None:
                # leg sans trip (impossible en pratique) : on s'abstient
                return None
            board = self.graph.stop_index.get(leg.from_id)
            alight = self.graph.stop_index.get(leg.to_id)
            if board is None or alight is None:
                return None
            km = self.leg_km(trip_idx, board, alight)
            region = self.trip_region(trip_idx)
            total_km += km
            regions.add(region)
            card_regions.add(region)
            entry = {"line": leg.line, "km": round(km, 1), "region": region}
            # §32 — un même train traversant plusieurs régions : on annonce le
            # découpage (billet par segment régional) sans changer le prix
            # mono/pluri, qui reste un billet unique dégressif par train.
            segs = self.trip_region_segments(trip_idx, board, alight)
            if len(segs) > 1:
                entry["segments"] = segs
                card_regions.update(s["region"] for s in segs)
            legs.append(entry)

        rule = "mono_region" if len(regions) == 1 else "pluri_region"
        if rule == "mono_region":
            region = legs[0]["region"]
            total_eur = self.fare(total_km, region)
        else:
            total_eur = round(sum(self.fare(jl["km"], jl["region"]) for jl in legs), 2)

        # Cartes de réduction : meilleure réduction par région, appliquée sur
        # le billet global (mono-région) ou sur chaque billet de tronçon. Une
        # carte n'est listée que si sa région d'application figure au trajet :
        # région majoritaire d'un train OU région traversée par un train
        # découpé en segments (§32).
        applied: list[dict] = []
        pay_by_region: dict[str, float] = {}
        for cid in cards or []:
            card = trainline_cards.card_by_id(cid)
            if card is None:
                continue
            info = self.card_info(card)
            if info["pay"] is None or info["region"] not in card_regions:
                continue
            applied.append({
                "id": cid, "name": card.get("name", cid),
                "shortName": card.get("shortName", card.get("name", cid)),
                "region": info["region"], "pay": info["pay"],
            })
            r = info["region"]
            if r not in pay_by_region or info["pay"] < pay_by_region[r]:
                pay_by_region[r] = info["pay"]

        if rule == "mono_region":
            pay = pay_by_region.get(legs[0]["region"])
            price_reduced_eur = self._discount(total_eur, pay) if pay is not None else total_eur
        else:
            reduced = 0.0
            for jl in legs:
                fare = self.fare(jl["km"], jl["region"])
                pay = pay_by_region.get(jl["region"])
                reduced += self._discount(fare, pay) if pay is not None else fare
            price_reduced_eur = round(reduced, 2)

        # Référence « billet unique » (mono/pluri), conservée dans split pour
        # l'affichage ; quand un train est découpé, le prix affiché devient le
        # total des billets découpés (le billet unique n'est pas vendable).
        single_full = total_eur
        single_reduced = price_reduced_eur

        # §32 — annonce du découpage intra-train : gares de jonction et
        # billetterie par segment régional (chaque segment tarifé avec la
        # meilleure carte de sa région). `price_reduced_eur` reste inchangé.
        split = None
        split_legs = [l for l in legs if l.get("segments")]
        if split_legs:
            segments_out: list[dict] = []
            junction_stations: list[str] = []
            split_regions: list[str] = []
            price_split = 0.0
            price_split_reduced = 0.0
            for l in split_legs:
                segs = l["segments"]
                for s in segs:
                    fare = self.fare(s["km"], s["region"])
                    pay = pay_by_region.get(s["region"])
                    red = self._discount(fare, pay) if pay is not None else fare
                    segments_out.append({**s, "fare_eur": fare, "fare_reduced_eur": red})
                    price_split += fare
                    price_split_reduced += red
                    if s["region"] not in split_regions:
                        split_regions.append(s["region"])
                for i in range(len(segs) - 1):
                    junction = segs[i]["to"]["name"]
                    if junction not in junction_stations:
                        junction_stations.append(junction)
            split = {
                "junction_stations": junction_stations,
                "regions": split_regions,
                "segments": segments_out,
                "price_split_eur": round(price_split, 2),
                "price_reduced_split_eur": round(price_split_reduced, 2),
                "single_ticket_eur": round(single_full, 2),
                "single_ticket_reduced_eur": round(single_reduced, 2),
            }

        if split is not None:
            total_eur = split["price_split_eur"]
            price_reduced_eur = split["price_reduced_split_eur"]

        return {
            "rule": rule,
            "regions": sorted(regions),
            "km": round(total_km, 1),
            "legs": legs,
            "price_normal_eur": total_eur,
            "price_reduced_eur": price_reduced_eur,
            "cards": applied,
            "split": split,
            "note": "prix estimés (modèle v1 calibré sur 3 prix observés Trainline, 12/08/2026)",
        }
