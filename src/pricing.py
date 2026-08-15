"""T12 — pricing.py : estimation tarifaire TER (MVP, modèle v2).

Modèle (cf. config/pricing.yaml) :
- distance ferroviaire d'un leg = somme des distances **PK** par hop
  (précalculées dans Graph.hop_km, cf. rfn.py) ; pour les hops sans ancres
  PK, repli haversine entre arrêts consécutifs × `rail_factor` ;
- région d'un train = région majoritaire de ses arrêts (config/station_regions) ;
- prix d'un billet : RÈGLE RÉGIONALE si la région en a une (escalier = grille
  forfaitaire, affine = a + b·d par palier) dans sa plage de distance validée
  (`max_km`) ; sinon formule de repli scale_region × (a·√km + b·km) arrondie
  aux 5 centimes, plancher min_eur ;
- agrégation : trajet mono-région -> un billet sur la distance totale cumulée
  (dégressivité globale) ; pluri-région -> somme des billets par tronçon ;
- segments interrégionaux « gap » (pas d'accord bilatéral, §33) : plein tarif,
  tarif = moyenne des barèmes des deux régions limitrophes (« matrice moyenne »,
  méthode 1 du doc des barèmes) ;
- cartes de réduction (T12) : chaque carte a une fraction `pay` du plein tarif
  (0.50 = -50 %) et une région d'application (déduite de son nom). La réduction
  ne s'applique QUE sur les segments de la région de la carte ; le tarif réduit
  retenu est la meilleure réduction parmi les cartes demandées.

Les prix sont des ESTIMATIONS : les règles par région sont validées sur les
prix réels vérifiés (14/08/2026, cf. data/pricing_rules.yaml) mais les tarifs
d'axe dérogatoires et la précision des distances restent des limites connues.
L'API marque ces prix comme estimés.
"""

from __future__ import annotations

import json
from math import asin, ceil, cos, radians, sin, sqrt
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
        self.region_rule = {name: v.get("rule") for name, v in cfg.get("regions", {}).items() if v.get("rule")}
        self._cross_rules: dict[tuple[str, str], str] = {}
        for a, b, rule in cfg.get("cross_region_rules", []):
            self._cross_rules[(a, b)] = rule
            self._cross_rules[(b, a)] = rule
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
    def _hop_km(self, trip_idx: int, k: int) -> float:
        """Distance ferroviaire du hop k -> k+1 du trip : distance PK
        précalculée (Graph.hop_km) si disponible, sinon haversine × rail_factor."""
        d = getattr(self.graph, "hop_km", {}).get((trip_idx, k))
        if d is not None:
            return d
        stops = self.graph.trips[trip_idx].stop_times
        s1, s2 = self.graph.stops[stops[k].stop], self.graph.stops[stops[k + 1].stop]
        return haversine_km(s1.lat, s1.lon, s2.lat, s2.lon) * self.rail_factor

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
                    km += self._hop_km(trip_idx, k)
            self._leg_km[key] = km
        return self._leg_km[key]

    # -------------------------------------- segments régionaux intra-train
    def _segment(self, trip_idx: int, i0: int, i1: int, region: str) -> dict:
        """Segment régional [positions i0..i1] d'un trip : gares extrêmes,
        distance (PK par hop, sinon haversine × rail_factor) et horaires."""
        stops = self.graph.trips[trip_idx].stop_times
        gs = self.graph.stops
        km = 0.0
        for k in range(i0, i1):
            km += self._hop_km(trip_idx, k)
        return {
            "region": region,
            "from": {"stop_area_id": gs[stops[i0].stop].id, "name": gs[stops[i0].stop].name},
            "to": {"stop_area_id": gs[stops[i1].stop].id, "name": gs[stops[i1].stop].name},
            "km": round(km, 1),
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
                if self._cross_rules.get((cur, region)) == "gap":
                    # §33/§34 — pas d'accord interrégional : segment plein
                    # tarif entre la dernière gare de la région sortante et la
                    # première de la région entrante (3 billets, ex. AURA↔PACA
                    # Pierrelatte -> Bollène-la-Croisière).
                    gap = self._segment(trip_idx, i - 1, i, cur)
                    gap["gap"] = True
                    gap["cross_region"] = region
                    segments.append(gap)
                    begin = i
                else:
                    # accord bilatéral : le segment suivant démarre à la gare de
                    # jonction (dernier arrêt de la région sortante) : les
                    # billets sont contigus (ex. Mâcon).
                    begin = i - 1
                cur = region
        segments.append(self._segment(trip_idx, begin, ai, cur))
        # Nettoyage (§32) : les segments de distance nulle (région limitrophe
        # tenant sur un seul arrêt, ex. Île-de-France à Paris Gare de Lyon) ne
        # font pas un billet à part. Ils sont absorbés dans le segment voisin
        # (pour les tout premiers, on remonte la gare de montée) ; les segments
        # consécutifs de même région sont ensuite fusionnés (jamais à travers
        # un segment `gap`, qui reste un billet à part, §33).
        useful = [i for i, seg in enumerate(segments) if seg["km"] > 0.0]
        if not useful:
            return segments
        out: list[dict] = []
        for idx, seg in enumerate(segments):
            if seg["km"] > 0.0:
                if seg.get("gap"):
                    out.append(dict(seg))
                elif out and out[-1]["region"] == seg["region"] and not out[-1].get("gap"):
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
    @staticmethod
    def _rule_price(rule: dict, km: float, scale: float = 1.0) -> float | None:
        """Prix d'un billet selon la règle régionale (paliers semi-ouverts
        [min, max)), ou None si le km est hors grille. `scale` est la
        recalibration régionale (affine seulement)."""
        t = rule.get("type")
        if t == "escalier":
            for lo, hi, price in rule.get("bands", []):
                if lo <= km < hi:
                    return float(price)
        elif t == "affine":
            for lo, hi, a, b in rule.get("bands", []):
                if lo <= km < hi:
                    p = (a + b * km) * scale
                    p = round(ceil(p / 0.10 - 1e-9) * 0.10, 2)  # décime supérieur
                    return max(p, float(rule.get("min_eur", 0.0)))
        elif t == "escalier_step":
            return ceil(km / rule["band"]) * rule["base"]
        return None

    def fare(self, km: float, region: str) -> float:
        """Prix estimé d'un billet sur `km` km dans `region`.

        Règle régionale si disponible dans sa plage validée ; sinon formule
        de repli scale_region × (a·√km + b·km). Le champ `max_eur` d'une
        règle plafonne le tarif (ex. Mobigo « 6 à 41 € maxi »)."""
        rule = self.region_rule.get(region)
        max_eur = rule.get("max_eur") if rule else None
        if rule is not None and km <= rule.get("max_km", float("inf")):
            scale = self.region_scale.get(region, self.default_scale)
            p = self._rule_price(rule, km, scale)
            if p is not None:
                return min(p, max_eur) if max_eur is not None else p
        # Repli formule (2) : le tarif réel ne redescend jamais sous le dernier
        # palier de la grille, et ne dépasse pas le plafond régional `max_eur`.
        fallback_min = rule["bands"][-1][2] if (rule and rule.get("type") == "escalier" and rule.get("bands")) else self.min_eur
        scale = self.region_scale.get(region, self.default_scale)
        raw = scale * (self.a * sqrt(km) + self.b * km) if km > 0 else 0.0
        p = max(fallback_min, round(round(raw / self.round_to) * self.round_to, 2))
        return min(p, max_eur) if max_eur is not None else p

    def _cross_fare(self, km: float, r1: str, r2: str) -> float:
        """Prix d'un segment interrégional « gap » (pas d'accord bilatéral) :
        moyenne des barèmes des deux régions limitrophes (méthode 1 du doc,
        « matrice moyenne »), arrondie au décime supérieur."""
        if km <= 0:
            return 0.0
        return round(ceil((self.fare(km, r1) + self.fare(km, r2)) / 2 / 0.10 - 1e-9) * 0.10, 2)

    def _segment_price(self, seg: dict, pay_by_region: dict[str, float]) -> tuple[float, float]:
        """(Plein tarif, tarif réduit) d'un segment régional : un segment `gap`
        (interrégional, pas d'accord) est plein tarif via la matrice moyenne
        (§33) ; les autres appliquent la meilleure carte de leur région."""
        if seg.get("gap") and seg.get("cross_region"):
            fare = self._cross_fare(seg["km"], seg["region"], seg["cross_region"])
        else:
            fare = self.fare(seg["km"], seg["region"])
        pay = None if seg.get("gap") else pay_by_region.get(seg["region"])
        red = self._discount(fare, pay) if pay is not None else fare
        return fare, red

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
            entry = {
                "line": leg.line,
                "from": self.graph.stops[board].name,
                "to": self.graph.stops[alight].name,
                "km": round(km, 1),
                "region": region,
            }
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
            for l in legs:
                segs = l.get("segments")
                if segs:
                    for s in segs:
                        # §33 — segment interrégional `gap` : plein tarif, aucune
                        # carte ne s'applique (pas d'accord entre les régions) ;
                        # tarif = moyenne des barèmes des deux régions (matrice).
                        fare, red = self._segment_price(s, pay_by_region)
                        segments_out.append({**s, "fare_eur": fare, "fare_reduced_eur": red})
                        price_split += fare
                        price_split_reduced += red
                        if s["region"] not in split_regions:
                            split_regions.append(s["region"])
                    # gare(s) de coupure : uniquement pour des billets contigus ;
                    # en présence d'un segment `gap`, l'annonce passe par les gares
                    # frontières du segment plein tarif (§33).
                    if not any(s.get("gap") for s in segs):
                        for i in range(len(segs) - 1):
                            junction = segs[i]["to"]["name"]
                            if junction not in junction_stations:
                                junction_stations.append(junction)
                else:
                    # leg simple (train sans découpage intra-train) : son billet
                    # unitaire entre en compte dans le total découpé.
                    fare = self.fare(l["km"], l["region"])
                    pay = pay_by_region.get(l["region"])
                    red = self._discount(fare, pay) if pay is not None else fare
                    price_split += fare
                    price_split_reduced += red
            split = {
                "junction_stations": junction_stations,
                "regions": split_regions,
                "segments": segments_out,
                "price_split_eur": round(price_split, 2),
                "price_reduced_split_eur": round(price_split_reduced, 2),
                "single_ticket_eur": round(single_full, 2),
                "single_ticket_reduced_eur": round(single_reduced, 2),
            }

        # Prix par tronçon pour l'affichage : un train découpé paie par
        # segment régional, un train simple un billet unitaire sur sa distance.
        for jl in legs:
            segs = jl.get("segments")
            if segs:
                f = fr = 0.0
                for s in segs:
                    fare, red = self._segment_price(s, pay_by_region)
                    f += fare
                    fr += red
                jl["fare_eur"] = round(f, 2)
                jl["fare_reduced_eur"] = round(fr, 2)
            else:
                fare = self.fare(jl["km"], jl["region"])
                pay = pay_by_region.get(jl["region"])
                jl["fare_eur"] = fare
                jl["fare_reduced_eur"] = self._discount(fare, pay) if pay is not None else fare

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
        }
