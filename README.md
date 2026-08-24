# PlanTER

![Build Android APK](https://github.com/muarf/PlanTER/actions/workflows/build-apk.yml/badge.svg)

> **https://ter.zvz.fr**

Moteur de recherche d'itinéraires **100% TER** en France — trains régionaux, avec estimation tarifaire et cartes de réduction régionales.

> **Prix indicatifs.** Les tarifs affichés sont des estimations basées sur les barèmes officiels. Des offres promotionnelles (web, apps, ventes flash) peuvent être moins chères. Seul le guichet ou le site de vente de la région donne le prix exact du jour.

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

- **Itinéraires 100% TER** : aucun TGV, aucun Intercités — uniquement des trains régionaux.
- **Tarification régionale** : prix estimés calibrés sur les barèmes officiels des 11 régions (AURA, BFC, Bretagne, CVL, GE, HdF, Normandie, Nouvelle-Aquitaine, Occitanie, PACA, PdL). L'Île-de-France n'est pas couverte.
- **Cartes de réduction** : toutes les cartes TER régionales, avec taux week-end/semaine pour Tempo, LibertiO', illico Liberté, Mobigo+ 26+.
- **Temps réel** : perturbations GTFS-RT, retards, suppressions.
- **Annuaire covoiturage** : groupes Signal classés par région pour les étapes sans rail.
- **App Android** : APK signé buildé en CI ([télécharger](https://github.com/muarf/PlanTER/releases)).
- **Anonymat** : requêtes d'itinéraires chiffrées + preuve de travail, pas de logs, pas de tracking, pas de cookies.
- **Self-hostable** : une image Docker/Podman pour faire tourner sa propre instance.

## Installation

```bash
# Cloner
git clone https://github.com/muarf/PlanTER.git
cd PlanTER

# Environnement Python
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Données : télécharge le GTFS SNCF + les feeds bus régionaux, filtre TER,
# valide, puis construit data/graph.bin (~10 min, comptez ~2 Go de RAM)
scripts/refresh_data.sh
```

`refresh_data.sh` est prévu pour la prod (il redémarre le service systemd à la
fin). Sur une autre machine, faites les étapes à la main :

```bash
.venv/bin/python -m src.download          # GTFS SNCF + TRSI
.venv/bin/python -m src.filter_ter        # filtre 100% TER (+ feeds bus)
.venv/bin/python -m src.validate_ter      # garde-fous

# Construction du graphe (TRSI et feeds bus ajoutés s'ils sont présents)
.venv/bin/python -m src.build_graph \
    --input data/ter/gtfs_ter.zip \
    --output data/graph.bin \
    --interchange config/interchange.yaml \
    --paris-links config/paris_links.yaml \
    --extra-input data/ter/gtfs_trsi.zip

# Lancer l'API + site web (l'API sert elle-même le site)
.venv/bin/python -m uvicorn src.api:app --host 0.0.0.0 --port 8000
# → http://localhost:8000
```

## API

| Endpoint | Description |
|---|---|
| `GET /v1/challenge` | Preuve de travail à résoudre (anti-abus) |
| `GET /v1/crypto/pubkey` | Clé publique pour chiffrer les paramètres de recherche |
| `POST /v1/journeys` | Itinéraires TER — payload **chiffré** + PoW en en-têtes |
| `GET`/`POST /v1/stations/search` | Recherche de gares |
| `GET /v1/cards` | Liste des cartes de réduction TER |
| `GET /v1/trips/{trip_id}/schedule` | Horaires d'un train + perturbations temps réel |
| `GET /v1/health` | État du service |
| `GET /docs` | Swagger interactif |

Les requêtes d'itinéraires ne transitent **jamais en clair** : le client
résout un mini-casse-tête (SHA256), récupère la clé publique puis chiffre ses
paramètres — ni les gares, ni l'heure, ni la date ne sont lisibles côté serveur.
Voir `web/app.js` pour une implémentation complète du flux.

### Exemple

```bash
curl "http://localhost:8000/v1/stations/search?q=Dijon"
```

## Héberger sa propre instance

Une seule image contient l'API **et** le site. Le graphe de routage (~120 Mo)
n'est pas embarqué : il est construit au premier démarrage depuis les open data
SNCF et les feeds bus régionaux (comptez ~10 min et ~2 Go de RAM au premier
lancement).

```bash
git clone https://github.com/muarf/PlanTER.git
cd PlanTER
docker compose up -d --build
# → http://localhost:8000
```

Variables utiles :

| Variable | Défaut | Rôle |
|---|---|---|
| `PLANTER_PORT` | `8000` | port exposé sur l'hôte |
| `REFRESH_ON_START` | `auto` | `auto` : construit le graphe s'il est absent · `force` : re-télécharge tout à chaque démarrage · `never` : ne touche à rien |
| `TER_FINDER_TELEGRAM_TOKEN` / `_CHAT_ID` | vide | alertes en cas d'échec du refresh |

Les données vivent dans `./data` et `./reports` (volumes) : elles survivent aux
recréations du conteneur. Pour rafraîchir les horaires :

```bash
docker compose down && REFRESH_ON_START=force docker compose up -d
```

**Podman** fonctionne tel quel aussi (`podman-compose up -d`) : rootless,
sans daemon — encore plus cohérent avec l'esprit du projet.

> L'instance officielle [ter.zvz.fr](https://ter.zvz.fr), elle, tourne sous
> systemd avec un cron quotidien sur `refresh_data.sh`.

## Architecture

```
src/
  raptor.py         Moteur McRAPTOR (Pareto 0-3 correspondances)
  rfn.py            Réseau ferroviaire (graph.bin, distances PK)
  pricing.py        Tarification régionale + cartes de réduction
  api.py            API REST FastAPI + site statique servi par l'API
  build_graph.py    Construction du graphe (TER + TRSI + feeds bus)
  graph.py          Modèle (Graph, Trip, StopArea)
  gtfs_rt.py        Temps réel GTFS-RT
  pow.py, crypto.py Anti-abus (preuve de travail) + chiffrement des requêtes
  trainline.py      Liens de réservation Trainline
config/
  pricing.yaml      Barèmes par région + cartes de réduction
  interchange.yaml, paris_links.yaml   Correspondances garanties, liaisons parisiennes
  bus_feeds.json    Feeds GTFS bus régionaux (TER+bus)
web/
  index.html, app.js, styles.css       Recherche et rendu
  about.html, cards.html, covoit.html, app.html, privacy.html
android/           Client Capacitor (APK buildé en CI)
fastlane/          Métadonnées F-Droid / IzzyOnDroid
scripts/
  refresh_data.sh   Pipeline données complet (prod)
docker/
  entrypoint.sh     Entrypoint conteneur (build graphe au 1er démarrage)
data/
  graph.bin         Graphe sérialisé — généré, jamais versionné
tests/             pytest (moteur, tarification, API)
```

## Données

- **GTFS SNCF** : horaires officiels (open data, licence ODbL) — trains régionaux.
- **Barèmes tarifaires** : extraits des CGV de chaque région (AURA, BFC, GE, Normandie, Occitanie, PACA, PdL…).
- **Cartes de réduction** : catalogue régional des cartes TER.

## Licence

Code source : **WTFPL** — DO WHAT THE FUCK YOU WANT TO.
Données SNCF : **ODbL** + Conditions Particulières d'utilisation (attribution requise).
