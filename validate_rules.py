"""Validation des règles tarifaires par région (pricing_rules.yaml)
contre les prix réels vérifiés en session.

Usage : python validate_rules.py
"""
import re
import sys
import yaml
from math import ceil

RULE_FILE = "/home/ubuntu/ter-finder-pricing/data/pricing_rules.yaml"


def round_up(x, step=0.10):
    return ceil(x / step - 1e-9) * step


def eval_rule(rule, km):
    """Renvoie le prix plein tarif 2nde classe prédit par la règle, ou None.
    Les paliers sont traités en intervalles contigus [min, next_min) : un km
    de 16,4 appartient au palier « 1 à 16 » (le doc tronque à l'entier)."""
    t = rule.get("type")
    scale = rule.get("scale", 1.0)
    if t in ("escalier", "affine"):
        bands = rule["bands"]
        for i, b in enumerate(bands):
            nxt = bands[i + 1]["min"] if i + 1 < len(bands) else float("inf")
            if b["min"] <= km < nxt:
                if t == "escalier":
                    return b["price"]
                p = (b["a"] + b["b"] * km) * scale
                p = round_up(p, 0.10)
                return max(p, rule.get("min_eur", 0.0))
        return None
    if t == "escalier_step":
        base = rule["base"]
        band = rule["band"]
        return round_up(base * ceil(km / band), 1.0) if base % 1.0 == 0 else base * ceil(km / band)
    if t == "formula":
        return round_up(scale * (rule["a"] * (km ** 0.5) + rule["b"] * km), 0.10)
    return None


# (région, origine-dest, km, prix réel vérifié, note outlier)
TESTS = [
    # --- Sud PACA (affine doc 3.4) ---
    ("Provence-Alpes-Côte d'Azur", "Marseille-Toulon", 66.9, 16.60),
    ("Provence-Alpes-Côte d'Azur", "Nice-Monaco", 15.9, 6.10),
    ("Provence-Alpes-Côte d'Azur", "Marseille-Aubagne", 16.9, 6.20),
    ("Provence-Alpes-Côte d'Azur", "Toulon-Bandol", 16.4, 6.10),
    ("Provence-Alpes-Côte d'Azur", "Marseille-Aix", 35.8, 10.90),
    ("Provence-Alpes-Côte d'Azur", "Marseille-Avignon", 121.3, 26.50),
    ("Provence-Alpes-Côte d'Azur", "Nice-Grasse", 52.9, 13.40),      # via Cannes (TER C3), distance réelle graphe
    ("Provence-Alpes-Côte d'Azur", "Toulon-Les Arcs", 66.0, 16.80),  # corrigé 14/08
    # --- Centre-Val de Loire (escalier Rémi) ---
    ("Centre-Val de Loire", "Tours-Blois", 59.0, 13.70),
    ("Centre-Val de Loire", "Vierzon-Bourges", 42.0, 10.30),
    ("Centre-Val de Loire", "Orléans-Tours", 115.0, 24.00),
    ("Centre-Val de Loire", "Tours-Amboise", 24.0, 6.80),
    ("Centre-Val de Loire", "Bourges-Châteauroux", 96.0, 20.50),
    ("Centre-Val de Loire", "Vierzon-Châteauroux", 60.0, 13.70),
    # --- Hauts-de-France (affine doc 3.1) ---
    ("Hauts-de-France", "Amiens-Arras", 68.3, 14.50),
    ("Hauts-de-France", "Valenciennes-Lille", 49.7, 11.10),
    ("Hauts-de-France", "Arras-Lille", 58.3, 13.10),   # arcades métropole
    ("Hauts-de-France", "Lille-Amiens", 126.6, 25.30), # écart résiduel
    ("Hauts-de-France", "Douai-Lille", 33.0, 8.60),    # arcades
    # --- AURA (CGV V202603 × 1.03, DK validées 14/08) ---
    ("Auvergne-Rhône-Alpes", "Lyon-Vienne", 32.1, 8.90),
    ("Auvergne-Rhône-Alpes", "Annecy-Chambéry", 53.2, 13.00),
    ("Auvergne-Rhône-Alpes", "Lyon-Chambéry", 106.8, 23.30),  # DK officielle ~106,8 (107,0 graphe → 23,40)
    ("Auvergne-Rhône-Alpes", "Lyon-Grenoble", 129.1, 27.90),
    ("Auvergne-Rhône-Alpes", "Lyon-Saint-Étienne", 59.0, 14.20),
    ("Auvergne-Rhône-Alpes", "Lyon-Valence", 105.2, 23.00),
    ("Auvergne-Rhône-Alpes", "Clermont-Lyon", 229.0, 44.00),
    ("Auvergne-Rhône-Alpes", "Clermont-Vichy", 55.0, 13.40),
    # --- Normandie (grille CGV Nomad 30/03/2026) ---
    ("Normandie", "Caen-Lisieux", 48.8, 8.80),
    ("Normandie", "Rouen-Dieppe", 59.5, 13.20),
    ("Normandie", "Rouen-Le Havre", 88.4, 17.60),
    ("Normandie", "Caen-Cherbourg", 131.4, 26.40),   # bande 126-150 km
    # --- Bretagne (escalier) ---
    ("Bretagne", "Brest-Morlaix", 59.7, 12.00),
    ("Bretagne", "Rennes-Vitré", 37.3, 12.00),
    ("Bretagne", "Saint-Brieuc-Guingamp", 30.2, 12.00),
    ("Bretagne", "Rennes-Saint-Brieuc", 101.4, 17.00),
    ("Bretagne", "Saint-Brieuc-Brest", 155.0, 22.00),
    ("Bretagne", "Rennes-Quimper", 239.5, 30.00),
    # --- Bourgogne-Franche-Comté (escalier Mobigo) ---
    ("Bourgogne-Franche-Comté", "Dijon-Beaune", 37.0, 13.00),
    ("Bourgogne-Franche-Comté", "Besançon-Dole", 46.1, 13.00),
    ("Bourgogne-Franche-Comté", "Besançon-Montbéliard", 78.5, 19.00),
    ("Bourgogne-Franche-Comté", "Dijon-Besançon", 93.0, 19.00),
    ("Bourgogne-Franche-Comté", "Besançon-Belfort", 96.2, 19.00),
    ("Bourgogne-Franche-Comté", "Dijon-Laroche-Migennes", 159.3, 31.00),
    # --- Grand Est (affine barème CGV V38, tarifs 01/01/2026) ---
    ("Grand Est", "Reims-Charleville-Mézières", 87.7, 20.00),
    ("Grand Est", "Strasbourg-Colmar", 65.8, 15.70),
    ("Grand Est", "Strasbourg-Mulhouse", 108.3, 23.40),
    ("Grand Est", "Strasbourg-Nancy", 149.6, 32.60),   # km réel ~151
    ("Grand Est", "Metz-Nancy", 54.4, 13.50),
    # --- Pays de la Loire (affine barème CGV 25/06/2026, plein tarif) ---
    # Nantes-Angers / St-Nazaire / Angers-Le Mans = prix ecco promo (barème > observé).
    ("Pays de la Loire", "Nantes-Le Mans", 185.0, 37.70),  # plein réel → km tarifaire ~185 (modèle 219,4 : distance surestimée)
    # --- Occitanie (liO, affine BKN lu par l'utilisateur) ---
    ("Occitanie", "Toulouse-Montauban", 50.5, 11.70),
    ("Occitanie", "Toulouse-Albi", 73.0, 16.20),
    ("Occitanie", "Montpellier-Béziers", 72.9, 15.50),
    ("Occitanie", "Perpignan-Narbonne", 63.9, 13.90),
    ("Occitanie", "Toulouse-Nîmes", 308.2, 50.10),
    # --- Nouvelle-Aquitaine (affine CGV, plein tarif guichet) ---
    ("Nouvelle-Aquitaine", "Bordeaux-Limoges", 224.6, 43.00),
    ("Nouvelle-Aquitaine", "Bordeaux-La Rochelle", 195.4, 37.60),
    ("Nouvelle-Aquitaine", "Bordeaux-Pau", 231.9, 43.80),
    ("Nouvelle-Aquitaine", "Bordeaux-Bayonne", 197.6, 38.10),
    ("Nouvelle-Aquitaine", "Angouleme-Bordeaux", 134.5, 28.60),
    ("Nouvelle-Aquitaine", "Perigueux-Bordeaux", 128.1, 26.90),
]


def main():
    with open(RULE_FILE) as f:
        rules = yaml.safe_load(f)["regions"]
    print(f"{'Région':<24}{'trajet':<28}{'km':>6}{'prédit':>8}{'réel':>8}{'écart':>7}")
    print("-" * 85)
    stats = {}
    for region, trajet, km, real in TESTS:
        rule = rules.get(region)
        if not rule or rule["type"] == "external":
            pred = "n/a"
            err = float("nan")
        else:
            p = eval_rule(rule, km)
            pred = f"{p:.2f}" if p is not None else "n/a"
            err = abs(p - real) if p is not None else float("nan")
        s = stats.setdefault(region, [0, 0.0, 0.0])
        if not (err != err):
            s[0] += 1
            s[1] += err
            s[2] = max(s[2], err)
        flag = "  <<" if err > 2.0 else ""
        print(f"{region:<24}{trajet:<28}{km:>6.1f}{pred:>8}{real:>8.2f}{err:>7.2f}{flag}")
    print("-" * 85)
    for region, (n, tot, worst) in sorted(stats.items(), key=lambda kv: -kv[1][1] / max(kv[1][0], 1)):
        print(f"{region:<24} n={n:>2}  erreur moy={tot / n:>5.2f} €  pire={worst:>5.2f} €")


if __name__ == "__main__":
    sys.exit(main())
