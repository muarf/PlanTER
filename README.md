# planTER

![Build Android APK](https://github.com/muarf/PlanTER/actions/workflows/build-apk.yml/badge.svg)

> Déploiement prod : https://ter.zvz.fr — APK Android buildée en CI (GitHub Actions).

Moteur de recherche d'itinéraires **100% TER** en France (trains régionaux + cars TER).
Voir [`PLAN.md`](PLAN.md) pour le cahier des charges complet et le découpage en tâches.

## Principe

Les outils existants privilégient TGV / Intercités et cachent les trajets régionaux.
planTER ne propose **que des trajets composés exclusivement de TER**, avec jusqu'à
**3 correspondances**, à partir des horaires officiels SNCF (données open data).

## Pipeline de données (Tâche T1)

Le GTFS SNCF publié est un fichier **global** (TGV + Intercités + TER). Les TER sont
isolés grâce au mode commercial encodé dans les `stop_id` de `stop_times.txt` :

```
StopPoint:OCE<MODE COMMERCIAL>-<code UIC de la gare>
```

Sont conservés uniquement les modes : `Train TER`, `Car TER`, `TramTrain`, `Train`.

### Utilisation

```bash
# 1. Télécharger le GTFS SNCF (data/raw/)
python3 -m src.download

# 2. Filtrer : ne garder que l'offre TER (data/ter/gtfs_ter.zip + rapport)
python3 -m src.filter_ter

# 3. Valider : tests de régression sur le GTFS-TER produit
python3 -m src.validate_ter

# 4. Vérifier la connectivité d'un trajet (outil de validation, remplacé par le moteur en T3)
python3 -m src.connectivity_check --from "Paris Gare de Lyon" --to "Dijon" \
    --date 2026-08-10 --time 07:00

# 5. Construire le graphe de routage (T2) -> data/graph.bin
python3 -m src.build_graph --input data/ter/gtfs_ter.zip --output data/graph.bin \
    --interchange config/interchange.yaml --paris-links config/paris_links.yaml

# 6. Moteur McRAPTOR (T3) : itinéraires Pareto 0-3 correspondances
python3 -m src.raptor --from "Paris Gare de Lyon" --to "Besançon Viotte" \
    --date 2026-08-10 --time 07:00                 # depart_after
python3 -m src.raptor --from "Paris Gare de Lyon" --to "Besançon Viotte" \
    --date 2026-08-10 --time 13:00 --mode arrive   # arrive_by
python3 -m src.raptor --from "Paris" --to "Mulhouse" --date 2026-08-10 \
    --time 06:00 --json                            # groupe « Paris » + JSON

# 7. Tests T3 + T4 (14 unitaires + 2 golden couvrant 13 cas datés, dont marches inter-gares)
python3 -m unittest tests.test_raptor -v
python3 -m unittest tests.golden_tests -v

# 8. API REST (T5) — FastAPI, Swagger sur /docs
python3 -m venv .venv                     # 1ère fois
.venv/bin/pip install -r requirements.txt # 1ère fois
.venv/bin/python -m uvicorn src.api:app --port 8000
curl "localhost:8000/v1/journeys?from=OCE87686006&to=OCE87718007&date=2026-08-10&time=07:00"
curl "localhost:8000/v1/stations/search?q=dijon"
curl "localhost:8000/v1/health"

# 9. Site web (T6) : SPA statique servie par l'API — ouvrir http://localhost:8000
#    (la page d'accueil est servie à la racine, les /v1/* restent prioritaires)

# 10. Tests T5 + T6 (21 tests)
.venv/bin/python -m unittest tests.test_api -v
```

### Arborescence

```
data/
  raw/           GTFS SNCF brut (versionné + latest.zip)
  ter/           GTFS-TER filtré (gtfs_ter.zip)
  graph.bin      graphe de routage sérialisé (cache T2/T3)
reports/
  filter_report.json    comptages avant/après + routes exclues
src/
  config.py      URL, whitelist/blacklist des modes commerciaux
  gtfs.py        I/O GTFS (CSV robuste, extraction du mode)
  download.py    T1 : téléchargement + vérification + versionnage
  filter_ter.py  T1 : filtrage TER + propagation à tous les fichiers GTFS
  validate_ter.py T1 : 5 tests de régression
  connectivity_check.py T1 : vérification qu'un trajet est faisable en TER (≤3 changements)
  build_graph.py T2 : construction du graphe + configs interchange/paris-links
  graph.py       T2/T3 : modèle (Graph, StopArea, Trip, index, place_groups)
  api.py         T5/T6 : API REST FastAPI (/v1/*) + SPA statique montée à la racine
  raptor.py      T3 : moteur McRAPTOR (DepartAfter / ArriveBy, Pareto, JSON)
web/
  index.html     T6 : page d'accueil (recherche), résultats, détail
  app.js         T6 : autocomplete, appels API, rendu des trajets
  styles.css     T6 : mobile-first, accessible
tests/
  test_raptor.py T3 : 14 tests unitaires
  golden_tests.py T4 : 13 golden datés (table exacte + contrôles de cohérence)
  test_api.py    T5/T6 : 21 tests d'intégration (contrats §7 + SPA §8)
```

Le détail de la T1 (découvertes sur les données, résultats de connectivité) est
consigné dans [`walkthrough.md`](walkthrough.md).

### Rapport de filtrage (export du 2026-08-10)

| | avant | après |
|---|---|---|
| trips | 47 296 | 37 004 |
| routes | 696 | 577 |
| stop_times | 409 812 | 349 167 |

Modes conservés : `Train TER` (30 973), `Car TER` (5 489), `TramTrain` (448), `Train` (94).
119 routes exclues (TGV Inoui, OUIGO, Intercités, Intercités de nuit, ICE, Lyria, cars à réservation).

### Validation

Les 5 tests de régression passent :
- **T1** aucun mode blacklisté ne subsiste ;
- **T2** la route `K7 | Paris - Lyon P D` (TER) est présente ;
- **T3** la route `611A | Paris - Besançon Viotte` (TGV) est absente ;
- **T4** chaque route conservée a au moins un trip TER ;
- **T5** le rapport de filtrage est présent et cohérent.

> Note : le `route_short_name` n'est pas unique (ex. « K7 » = plusieurs lignes
> physiques). L'identité de ligne se fait par `route_id`. Voir `walkthrough.md` §4.3.

## Licence

Données SNCF : **ODbL** + Conditions Particulières d'utilisation (attribution requise).
Code source : projet privé.
