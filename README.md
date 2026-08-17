# planTER

![Build Android APK](https://github.com/muarf/PlanTER/actions/workflows/build-apk.yml/badge.svg)

> **https://ter.zvz.fr**

Moteur de recherche d'itinéraires **100% TER** en France — trains régionaux et cars TER, avec estimation tarifaire et cartes de réduction.

## Mais au fait, pourquoi faire tout ça ?

**Parce qu'on vous cache des choses.**
Aucun site de réservation survitaminé, ni aucun algorithme officiel ne vous avouera qu'il est possible de traverser le pays en enchaînant de longues et magnifiques correspondances régionales. Ils préfèrent vous vendre de la grande vitesse ; nous avons décidé de cartographier les chemins de traverse.

**Parce que l'aventure n'attend pas les baisses de prix.**
La vraie liberté, c'est de pouvoir faire ses bagages à la dernière minute sans se faire plumer. Vous partez quand vous le décidez, avec la garantie d'un coût fixe — un tarif qui devient vraiment avantageux pour peu qu'on ait les bonnes cartes de réduction. Fini l'angoisse des prix qui s'envolent à l'approche du départ.

**Parce que le cash a encore son utilité.**
On préfère glisser quelques pièces et billets dans l'automate d'une petite gare de campagne que de laisser des traces numériques partout. Un paiement anonyme, dans ces petits arrêts hors du temps, souvent épargnés par l'œil omniprésent de la vidéosurveillance. Juste vous, le distributeur ou le chef de gare, et le chant des oiseaux (quand il en reste...).

**Parce qu'il est urgent de ralentir.**
Dans un monde obnubilé par l'immédiateté, la rentabilité absolue et les TGV filant à 300 km/h, on a simplement envie de prendre notre temps. Regarder le paysage qui défile à l'allure d'un train régional, c'est choisir le voyage plutôt que la simple destination.

**Et enfin...**
*Car, au fond, pourquoi pas ?*

## Fonctionnalités

- **Itinéraires 100% TER** : aucun TGV, aucun Intercités — uniquement des trains régionaux et cars TER, avec jusqu'à 3 correspondances.
- **Tarification régionale** : prix estimés calibrés sur les barèmes officiels de 8 régions (AURA, BFC, Bretagne, CVL, GE, HdF, Normandie, Occitanie, PACA, PdL).
- **Cartes de réduction** : 46 cartes TER régionales avec taux week-end/semaine.
- **Temps réel** : perturbations GTFS-RT, retards, suppressions.
- **PWA** : installable sur mobile, fonctionne hors-ligne (recherche côté serveur).
- **Anonymat** : pas de logs, pas de tracking, pas de cookies.

## Installation

```bash
# Cloner
git clone https://github.com/muarf/PlanTER.git
cd PlanTER

# Environnement Python
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Construire le graphe de routage (nécessite le GTFS-TER filtré)
.venv/bin/python -m src.build_graph \
  --input data/ter/gtfs_ter.zip \
  --output data/graph.bin \
  --interchange config/interchange.yaml \
  --paris-links config/paris_links.yaml

# Lancer l'API + site web
.venv/bin/python -m uvicorn src.api:app --host 0.0.0.0 --port 8000
# → http://localhost:8000
```

## API

| Endpoint | Description |
|---|---|
| `GET /v1/journeys` | Itinéraires TER (date, heure, cartes de réduction) |
| `GET /v1/stations/search` | Recherche de gares |
| `GET /v1/cards` | Liste des cartes de réduction TER |
| `GET /v1/health` | État du service |
| `GET /docs` | Swagger interactif |

### Exemple

```bash
curl "http://localhost:8000/v1/journeys?from=Dijon&to=Lyon&date=2026-08-20&time=08:00"
```

## Architecture

```
src/
  raptor.py         Moteur McRAPTOR (Pareto 0-3 correspondances)
  rfn.py            Réseau ferroviaire (graph.bin, distances PK)
  pricing.py        Tarification régionale + cartes de réduction
  api.py            API REST FastAPI + SPA statique
  build_graph.py    Construction du graphe de routage
  graph.py          Modèle (Graph, Trip, StopArea)
  gtfs_rt.py        Temps réel GTFS-RT
  trainline.py      Liens de réservation Trainline
config/
  pricing.yaml      Barèmes par région + cartes de réduction
  station_regions.json   Gares → régions
  trainline_cards.json   Catalogue des cartes TER
web/
  index.html        Page d'accueil (recherche)
  app.js            Autocomplete, appels API, rendu
  styles.css        Mobile-first, accessible
  about.html        Pourquoi planTER
  cards.html        Guide des cartes de réduction
  privacy.html      Politique de confidentialité
data/
  graph.bin         Graphe sérialisé (cache)
  ter/              GTFS-TER filtré
tests/
  test_raptor.py    Tests moteur
  test_pricing.py   Tests tarification
  test_api.py       Tests API
```

## Données

- **GTFS SNCF** : horaires officiels (open data, licence ODbL).
- **Barèmes tarifaires** : extraits des CGV de chaque région (AURA, BFC, GE, Normandie, Occitanie, PACA, PdL).
- **Cartes de réduction** : catalogue Trainline (46 cartes, 11 régions).

## Licence

Code source : projet privé.
Données SNCF : **ODbL** + Conditions Particulières d'utilisation (attribution requise).
