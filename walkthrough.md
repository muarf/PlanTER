# TER Finder — Walkthrough T1

Document de suivi de la **Tâche T1** (pipeline de données + filtre TER), exécutée le
10/08/2026. Il consigne les commandes exactes, l'arborescence produite et les
découvertes sur les données qui guident les tâches suivantes (T2, T3).

---

## 1. Arborescence

```
ter-finder/
├── PLAN.md                cahier des charges (T1→T9)
├── README.md              documentation utilisateur (pipeline T1)
├── requirements.txt
├── .gitignore
├── data/
│   ├── raw/
│   │   ├── gtfs_20260810_061239.zip     GTFS SNCF brut versionné
│   │   ├── latest.zip                    copie « dernier connu »
│   │   └── manifest.json                 métadonnées du téléchargement
│   └── ter/
│       └── gtfs_ter.zip                  GTFS-TER filtré (4,0 Mo)
├── reports/
│   └── filter_report.json                comptages avant/après + routes exclues
└── src/
    ├── __init__.py
    ├── config.py           URL, whitelist/blacklist des modes, constantes
    ├── gtfs.py             I/O GTFS (CSV robuste) + extract_mode()
    ├── download.py         T1 : téléchargement + vérification + versionnage
    ├── filter_ter.py       T1 : filtrage TER + propagation à tous les fichiers
    ├── validate_ter.py     T1 : 5 tests de régression
    └── connectivity_check.py  T1 : vérificateur de connectivité (outil de validation)
```

## 2. Commandes exécutées

```bash
# 1. Téléchargement (sans `requests` : pipe curl → python3, stdin)
python3 -m src.download

# 2. Filtrage TER
python3 -m src.filter_ter

# 3. Validation (5 tests)
python3 -m src.validate_ter

# 4. Vérificateur de connectivité (outil de validation, cf. §5)
python3 -m src.connectivity_check \
    --pairs "Paris Bercy|Dijon;Paris Gare de Lyon|Dijon" \
    --date 2026-08-10 --time 07:00 --max-transfers 3
```

## 3. Rapport de filtrage (export du 2026-08-10)

| Fichier | avant | après |
|---|---|---|
| trips | 47 296 | 37 004 |
| routes | 696 | 577 |
| stop_times | 409 812 | 349 167 |
| stops | 8 906 | 8 293 |
| calendar_dates | 236 639 | 178 355 |

Modes conservés : `Train TER` (30 973), `Car TER` (5 489), `TramTrain` (448), `Train` (94).

## 4. Découvertes sur les données (importantes pour T2/T3)

### 4.1 Modèle « NewTripId » : un trip = une instance datée

- **37 004 trips, tous avec un `trip_id` unique.**
- Chaque `trip_id` se termine par la date de circulation (ex. `…::87723197:87713040:10:1511:20260915`).
- 5 788 `service_id` différents ; chaque service référence ses dates dans `calendar_dates.txt`
  (moyenne ≈ 31 dates par service, max 126).
- **Vérification de cohérence** : pour 36 787 trips avec date embarquée, la date est
  toujours dans les dates du `service_id` (**0 incohérence**). 217 trips sans date
  embarquée restent correctement couverts par leur `service_id`.
- **Règle à utiliser dans le moteur** : *un trip circule le jour J si
  `J ∈ dates(service_id)`*. C'est cette règle qu'appliquera le builder (T2).

### 4.2 Couverture de dates

- 133 jours couverts : **2026-08-09 → 2026-12-19** (et non 151 — à mettre à jour dans le plan).
- Aucune requête hors plage ne doit être acceptée par l'API (erreur 400).

### 4.3 Le `route_short_name` n'identifie PAS une ligne physique

La ligne « K7 » porte **plusieurs** lignes physiques distinctes (route_id différents) :

| route_id | short_name | ligne physique |
|---|---|---|
| `FR:Line::B10C45A0-…` | K7 | Paris Gare de Lyon/Bercy – Dijon – Lyon |
| `FR:Line::7B48FDEF-…` | K7 | Marseille – Miramas – Arles – Avignon (TER) |

Conséquence : **toute identité de ligne doit reposer sur le `route_id`** ; le
`short_name` n'est qu'une étiquette d'affichage (peut être affiché en doublon dans l'UI).

### 4.4 Service irrégulier des lignes « directes »

Le direct K7 Paris Gare de Lyon → Dijon → Lyon **ne circule pas tous les jours** :
- 10/08/2026 : direct à 07:34 → Dijon 10:33 → Lyon Part Dieu 12:44.
- 15/09/2026 : pas de direct ; il faut 1 changement via Laroche-Migennes.

Le moteur doit donc **toujours résoudre la validité à la date demandée**
(`calendar_dates`), jamais supposer une fréquence. C'est un comportement
fondamental, pas un cas à part.

### 4.5 Formats à gérer

- Dates `calendar_dates` : `YYYYMMDD` **sans tirets**.
- Heures `stop_times` : `HH:MM` **ou** `HH:MM:SS`, pouvant dépasser 24 h
  (ex. `26:15` → 1575 min après minuit) ; normaliser en minutes.

### 4.6 Arrêt des gares parisiennes

- « Paris Bercy » = `StopArea:OCE87686667`, « Paris Gare de Lyon » =
  `StopArea:OCE87686006` (plusieurs StopPoints « Hall 1 - 2 »). Le départ réel
  du TER K7/P34 est à **Gare de Lyon**, et le départ de l'essentiel des lignes
  Bourgogne/Nivernais à **Bercy**. Les deux sont des gares distinctes : la v1
  devra les garder distinctes (cf. plan §5.3).

## 5. Vérificateur de connectivité (`connectivity_check.py`)

**Rôle** : outil de validation de la T1 — vérifier sur un couple de gares et une
date que l'offre TER filtrée permet bien le trajet avec ≤ `--max-transfers`
correspondances. Il sera **remplacé** par le moteur McRAPTOR de la T3.

Implémentation : RAPTOR simplifié (parcours par rounds, « départ au plus tôt /
arrivée au plus tôt »), reconstruction du chemin par backpointers.

### Usage

```bash
python3 -m src.connectivity_check \
    --from "Paris Gare de Lyon" --to "Dijon" \
    --date 2026-08-10 --time 07:00 --max-transfers 3

# lot de couples (attention aux apostrophes : utiliser les noms exacts)
python3 -m src.connectivity_check --pairs "Paris Bercy|Dijon;Lyon Part Dieu|Besançon Viotte" \
    --date 2026-08-10 --time 08:00
```

### Résultats de référence (date 2026-08-10, départ 08:00)

| Trajet | Changements | Itinéraire |
|---|---|---|
| Paris Gare de Lyon → Besançon Viotte | 1 | K7 Gare de Lyon → Dijon, puis C11 → Besançon Viotte (arr. 12:04) |
| Paris Gare de Lyon → Lyon Part Dieu | 0 | K7 direct 07:34 → 12:44 |
| Paris Bercy → Dijon | 0 | K7 Bercy → Dijon (arr. 16:26) |
| Lyon Part Dieu → Besançon Viotte | 0 | K13 direct |
| Toulouse → Marseille | 2 | K3 → Narbonne, K8 → Avignon Centre, K7 → Marseille Saint-Charles, K6 → Marseille Blancarde (arr. 17:52) |
| Strasbourg → Lyon | 3 | K200 → Mulhouse, C13 → Belfort, C3 → Besançon, P33 → Bourg-en-Bresse, C23 → Lyon |
| Nantes → Lyon | 2 | K1 → Tours, P2 → Lozanne, C22 → Lyon Vaise |
| Clermont-Ferrand → Toulouse | 0 | P27 direct |
| Rennes → Nantes | 0 | K5 direct (arr. 08:52) |

**Lot complet exécuté en 2,6 s** avec `--pairs` (date 2026-08-10, 07:00).

**Cas sans solution TER (≤ 3 changements, le 15/09) :** Lille → Lyon,
Rennes → Bordeaux, Paris → Vittel, Bordeaux → Saint-Étienne, Brest → Nice
(certains peuvent être atteignables à d'autres dates/avec plus de changements —
à re-vérifier au cas par cas).

**Trajet retour** Besançon Viotte → Paris **Bercy** (10/08) : **1 changement**, et
le validateur retrouve exactement l'itinéraire réel du soir :

```
✓ Besançon Viotte -> Paris Bercy (départ 18:16) :
    1 changement(s), arrivée 22:37
    Besançon Viotte [C1] Dijon -> Dijon [K7] Paris Bercy Bourg. Pays d'Auv.
```

Légende : la 1re étape est le car C1 « MOBIGO » (trip 894264, 18:16→19:20), la 2e
le TER K7 train n° 17764 (19:34→22:37). Attention : le K7 retour vers Paris
arrive à **Bercy**, pas à Gare de Lyon — d'où le « aucun trajet » initial quand
on cherchait vers `Paris Gare de Lyon` (destination différente, pas une offre
unidirectionnelle).

### Bug corrigé : boucle infinie dans `_reconstruct`

Le 10/08, certains couples (Rennes→Nantes, Paris Bercy→Dijon) ne rendaient
jamais la main (puis OOM). Cause : quand l'arrivée *planifiée* d'un train à sa
gare d'embarquement est antérieure à notre arrivée *réelle*, on écrivait
`prev[gare] = (gare, trip)` — une auto-boucle — et la reconstruction des legs
`while cur != origin` ne se terminait jamais. Correctifs :
- ne jamais traiter la gare d'embarquement comme une amélioration (`a2 == area`
  → `continue`) ;
- garde-fou anti-cycle dans `_reconstruct` (set `seen`) pour les routes circulaires.

## 6. Prochaines étapes

- **T3** : moteur McRAPTOR sur le graphe (§7).
- **T4** : golden tests sur les trajets du §5 (cas référence : Paris Bercy / Gare
  de Lyon → Dijon, 0 ou 1 changement).

## 7. Exécution T2 (builder de graphe) — 10/08/2026

### Commande

```bash
python3 -m src.build_graph \
    --input data/ter/gtfs_ter.zip --output data/graph.bin \
    --interchange config/interchange.yaml --paris-links config/paris_links.yaml
```

Résultat :

```
[build] graphe : 3462 gares, 577 lignes, 37004 trips, 20260809-20261219
[build] ✓ Acceptation T2 (2026-08-10) : K7 Paris GDL -> Dijon, C11 Dijon -> Besançon, correspondance OK
[build] cache  : data/graph.bin (19 923 Ko, build 4,1 s)
[build] load   : 0,43 s (✓ < 2 s) — 37004 trips rechargés
```

### Découvertes / bugs corrigés pendant le build

1. **Alias auto-référents** : l'itération sur `search_index.get(target_norm)` qui
   était le *même objet* que la liste destination de l'alias (ex. « bordeaux
   saint jean » alias d'elle-même) → boucle infinie. Corrigé en copiant la liste
   pendant l'itération, puis en dédupliquant via des `set` (un alias qui répète le
   nom normalisé d'une gare ne doit pas dupliquer son entrée).
2. **Ambiguité de résolution de noms** : la règle `len(hits)==1` cassait la
   résolution des configs dès qu'une gare avait un doublon réel (Albias, Grenoble,
   Hundling Hôtel de Ville, Pouzauges = 2 StopAreas distincts) *ou* des entrées
   dupliquées par l'alias. L'index dédupliqué restaure les noms uniques (Dijon,
   Paris Gare de Lyon, Lyon Part Dieu, …).
3. **Configs corrigées** : noms de gares exacts dans `interchange.yaml` /
   `paris_links.yaml` (ex. « Paris Gare du Nord » et « Paris Montparnasse Hall 1
   - 2 », pas « Paris Nord » / « Paris Montparnasse »).
4. **Autocomplete (§5.4)** : `find_stops` fait maintenant une recherche exacte
   puis préfixe puis sous-chaîne sur les noms normalisés, avec déduplication.

### Vérifications

```bash
python3 -m src.validate_ter                       # 5/5 OK
python3 -m src.connectivity_check --pairs "Paris Gare de Lyon|Dijon" --date 2026-08-10 --time 07:00
# ✓ Paris Gare de Lyon -> Dijon : 0 changement(s), K7 direct, arrivée 10:33
```

### Recherche de gares (autocomplete)

```python
g = Graph.load(Path("data/graph.bin"))
g.find_stops("lyon")        # [Lyon Gorge de Loup, Lyon Jean Macé, Lyon Part Dieu, ...]
g.find_stops("paris")       # Paris Austerlitz, Paris Bercy, Paris Est, ...
g.find_stops("besancon")    # Besançon Franche-Comté TGV, Besançon Mouillère, Besançon Viotte
g.min_transfer[2627]        # 8 min à Dijon (config interchange)
```

### « Paris toutes gares » (§5.5)

La recherche accepte « Paris » / « Paris toutes gares » comme un **groupe** de
7 gares (Est, Nord, Saint-Lazare, Montparnasse Hall 1 - 2, Austerlitz, Gare de
Lyon Hall 1 - 2, Bercy) : atteindre n'importe laquelle satisfait la recherche.
Implémenté par `Graph.place_groups` + `Graph.resolve_place()` et, côté
vérificateur, par `_find_areas()` (recherche multi-origine / multi-destination).

```bash
# Retour du soir (itinéraire réel) : le K7 17764 arrive à Paris Bercy
python3 -m src.connectivity_check --from "Besançon Viotte" --to "Paris" --date 2026-08-10 --time 18:16
# ✓ 1 changement, arrivée 22:37 : C1 (MOBIGO) Besançon -> Dijon, K7 -> Paris Bercy

# Aller matin : le K7 part de Paris Gare de Lyon
python3 -m src.connectivity_check --from "Paris" --to "Besançon Viotte" --date 2026-08-10 --time 07:00
# ✓ 1 changement, arrivée 12:04 : K7 Paris Gare de Lyon -> Dijon, C11 -> Besançon Viotte
```

## 8. Exécution T3 (moteur McRAPTOR) — 10/08/2026

### Commande

```bash
python3 -m src.raptor --from "Paris Gare de Lyon" --to "Besançon Viotte" --date 2026-08-10 --time 07:00
# [raptor] Paris Gare de Lyon -> Besançon Viotte | 2026-08-10 07:00 | mode=depart | max_transfers=3 | 342 ms
#   ✓ 1 correspondance(s) : départ 07:34 -> arrivée 12:04 (durée 270 min)
#       07:34 Paris Gare de Lyon Hall 1 - 2 [K7 N17769] -> 10:33 Dijon
#       11:09 Dijon [C11 N894213] -> 12:04 Besançon Viotte
```

Options : `--mode arrive` (ArriveBy), `--max-transfers` (0–6), `--vehicle train_only`,
`--json` (sortie structurée §6.5).

### Résultats de référence (2026-08-10)

| Requête | Mode | Résultat |
|---|---|---|
| Paris Gare de Lyon → Besançon Viotte 07:00 | depart | 1 chgt — K7 07:34 → Dijon 10:33, C11 11:09 → Besançon 12:04 |
| Besançon Viotte → Paris 18:00 | depart | 1 chgt — C1 18:16 (MOBIGO N894264) → Dijon 19:20, K7 19:34 → **Bercy** 22:37 |
| Paris → Saint-Étienne Châteaucreux 07:00 | depart | 1 chgt — K7 → Lyon Part Dieu 12:44, C18 12:54 → 13:40 |
| Paris → Mulhouse 06:00 | depart | 0 chgt — K4 06:05 Paris Est → 11:13 (groupe « Paris ») |
| Paris → Grenoble 07:00 | depart | 2 chgt — K7 → K6 → K6 (arr. jour+1) |
| Paris Gare de Lyon → Besançon Viotte ≤ 13:00 | arrive | 07:34 → 12:04 (ArriveBy) |
| Paris Gare de Lyon → Besançon Viotte 03:00 | depart | 07:34 → 12:04 (1er train après minuit) |
| Lyon Part Dieu → Lille Flandres 07:00 | depart | **Aucun** (exige du TGV — cohérent) |
| Paris Bercy → Mulhouse 07:00 | depart | 3 chgt — 2 marches intra-Paris + K4 (Belfort) + C13, arr. 13:39 |
| Paris Bercy → Dijon 07:00 | depart | 1 chgt — marche 07:00 Bercy → GDL 07:10, K7 07:34 → 10:33 |
| Paris Gare de Lyon → Nevers 08:00 | depart | 1 chgt — marche GDL → Bercy 08:10, K2+ 09:10 → 11:37 |

### Bugs découverts et corrigés pendant T3

1. **Ligne avec terminus différents selon les trips** (découverte majeure) : le K7 a
   des trips `Paris GDL → Lyon` *et* des trips `Lyon → Paris Bercy`. Les ensembles
   d'arrêts par route étaient dérivés du **premier trip** → « Paris Gare de Lyon »
   absent de la route K7 → le trip de 07:34 jamais balayé. Corrigé : `_route_stop_sets`
   = **union** des arrêts sur tous les trips de la route (fix dans `raptor.py`).
2. **Bug McRAPTOR classique sur `new_marked`** : l'heure d'embarquement d'un arrêt
   était verrouillée à sa **première** découverte dans le round ; une arrivée plus
   précoce (même round) améliorait `arr[a2]` mais pas le marquage → le train suivant
   était sauté (ex. retour du soir : Dijon marqué 19:59 au lieu de 19:28 → K7 19:34
   raté → « Aucun trajet »). Corrigé : `new_marked[a2]` suit **chaque** amélioration.
3. **Bug T2 caché (`routes_by_stop`)** découvert par le test de parité : le `seen`
   contenait la *route* au lieu de l'*arrêt* → une ligne n'était listée qu'au
   **premier arrêt** de ses trips. Paris GDL / Dijon marchaient « par chance » (des
   trips K7 y commencent), mais Lyon Part Dieu n'avait aucun trip C23 qui y commence
   → la correspondance K7→C23 de 12:51 était invisible. Corrigé dans `build_graph.py`
   (dédup par `st.stop`) + test de non-régression couvrant **tous** les (trip, arrêt).
4. **Config `interchange.yaml`** : `Lyon Part Dieu: 10 → 7` — la correspondance réelle
   K7 12:44 → C23 12:51 (7 min) est désormais prise en compte (parité exacte avec
   connectivity_check : arrivée 12:59).

### Parité avec connectivity_check (oracle)

16 couples (dont tous les cas réels) : arrivées **identiques sur 16/16** quand on
compare le raptor **sans les arcs piétons** (l'oracle ne connaît pas `paris_links`).
Avec les marches activées, 4 écarts apparents, tous **légitimes** :
- GDL → Besançon / Nevers / Lyon Perrache : l'oracle répond « aucun » (pas d'arcs
  piétons) alors que le moteur trouve une vraie solution via une marche inter-gares.
- Bercy → Mulhouse : le raptor domine l'oracle — 3 chgt / arr. 13:39 (marches + K4 + C13)
  contre la chaîne K2+→K6→K5→K4 de l'oracle (arr. 21:34).

### Legs de marche inter-gares (§5.3)

- `transfer_walk[b] = (a, heure)` posé à chaque amélioration piétonne (origine +
  relaxation entre rounds) ; la marche ne consomme pas de round.
- Reconstruction (`_best_parent`, `_walk_leg`) : un leg `walk` a `route_id`/`line`/`trip_id`
  vides et peut connecter à `k == 0` (marche d'origine seule).
- Garde-fous : plafond `MAX_WALK_LEGS = 2` et **cohérence temporelle stricte** entre
  legs (aucun leg ne « part » avant l'arrivée du précédent) — rejette les chaînes de
  marche Frankenstein qui mélangeaient des rounds différents.

### Performance

```
graphe : data/graph.bin (19 942 Ko, load 0,43 s)
100 requêtes aléatoires (3 dates, warm) : 76 ms/requête   (< 100 ms ✓)
requête froide (vues à construire) : ~300-370 ms
```

### Tests

```bash
python3 -m unittest tests.test_raptor -v   # 14 tests, OK (2,2 s)
```

Couvre : 0/1/2 chgt, pas de solution, nuit, ArriveBy, `train_only`, groupe
« Paris », marches inter-gares, JSON, non-régression `routes_by_stop`,
non-régression C23 → Lyon Part Dieu.

## 9. Exécution T4 (golden tests) — 10/08/2026

### Table dorée datée

```bash
python3 -m unittest tests.golden_tests -v   # 16 tests, OK (2,8 s)
```

| Date | Origine → Destination | Départ | Résultat exact (transf., dép. → arr.) |
|---|---|---|---|
| lun. 10/08 | Besançon Viotte → Dijon | 12:00 | 0 chgt — 12:06 → 13:02 (C11) |
| lun. 10/08 | Paris GDL → Besançon Viotte | 07:00 | 1 chgt — 07:34 → 12:04 (K7 + C11) |
| lun. 10/08 | Besançon Viotte → Paris | 18:00 | 1 chgt — 18:16 → 22:37 (C1 + K7 → Bercy) |
| lun. 10/08 | Paris → Mulhouse | 06:00 | 0 chgt — 06:05 → 11:13 (K4, groupe « Paris ») |
| lun. 10/08 | Paris → Grenoble | 07:00 | 2 chgt — 07:34 → 24:31 (jour+1) |
| lun. 10/08 | Paris Bercy → Dijon | 07:00 | 1 chgt — 07:00 → 10:33 (marche + K7) |
| lun. 10/08 | Paris GDL → Nevers | 08:00 | 1 chgt — 08:00 → 11:37 (marche + K2+) |
| lun. 10/08 | Toulouse → Clermont-Ferrand | 08:00 | 0 chgt — 13:03 → 19:04 (P27) |
| lun. 10/08 | Lyon Part Dieu → Lille | 07:00 | **aucun** (TGV obligatoire) |
| lun. 10/08 | Paris → Nice | 08:00 | **aucun** |
| lun. 10/08 | Paris → Vittel | 08:00 | ≥ 1 chgt (**pas de direct K6 le lundi**) |
| dim. 16/08 | Paris → Vittel | 08:00 | 0 chgt — 08:21 → 12:42 (K6 N840451, **week-end seulement**) |
| lun. 10/08 | Paris GDL → Besançon Viotte | ≤ 13:00 | arrive — 07:34 → 12:04 (ArriveBy) |

### Cas daté Vittel (contrôle croisé)

Route `K6 | PARIS - VITTEL` : les directs Paris → Vittel ne circulent que les
**week-ends** (09, 14, 15, 16, 21, 23, 28, 30 août 2026) — pas le lundi 10/08. La
table l'enferme : 1 chgt via Nancy (K850) le lundi, direct le dimanche. C'est le
piège de §4.5/§T4 : un « trajet direct » figé sans date serait faux la moitié de la
semaine.

### Contrôles de cohérence (`_check_journey`)

Chaque itinéraire retourné est vérifié : heures strictement croissantes,
continuité des stop_area entre legs, `min_transfer` respecté à chaque embarquement
train, `transfers == len(legs) - 1`, legs de marche bien formés (champs ligne vides).
Un sweep de 8 requêtes supplémentaires (dont 2 à date différente) ré-applique ces
contrôles sur tous les trajets renvoyés.

## 10. Exécution T5 (API REST FastAPI) — 10/08/2026

### Installation

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt    # fastapi, uvicorn, requests
```

### Démarrage et exemple canonique (§7.4)

```bash
.venv/bin/python -m uvicorn src.api:app --port 8000
# Swagger : http://localhost:8000/docs

curl "localhost:8000/v1/journeys?from=OCE87686006&to=OCE87718007\
&date=2026-08-10&time=07:00&max_transfers=3"
# → 1 correspondance, K7 07:34 -> Dijon 10:33, C11 11:09 -> Besançon 12:04
curl "localhost:8000/v1/stations/search?q=dijon"
curl "localhost:8000/v1/health"
# → {"status":"ok","data_date":"2026-12-19","coverage_start":"2026-08-09",
#    "coverage_end":"2026-12-19","stations":3462}
```

### Endpoints (§7.2) et contrats d'erreur (§7.3)

- `GET /v1/journeys` : `from`/`to` acceptent un `stop_area_id` nu ou préfixé
  `StopArea:`, des coordonnées « lat,lon » (gare la plus proche), un nom de gare
  ou un groupe (« Paris ») ; `datetime_represents=departure|arrival`,
  `max_transfers=0..6`, `vehicle=all|train_only`, `count=1..20`.
- Erreurs : 404 `STATION_NOT_FOUND` (+ suggestions), 400 `INVALID_DATE` (format ou
  hors plage `2026-08-09 → 2026-12-19`) / `INVALID_TIME`, 422 paramètres hors
  domaine, 200 `{ journeys: [] }` si aucun trajet.
- Les ids exposés dans les réponses sont **nus** (`OCE87686006`, §6.5/§7.4) alors
  que le graphe les stocke préfixés `StopArea:` — normalisation à la frontière API.

### Fidélité des dates

`_iso` datte désormais les arrivées **jour+1** (Paris → Grenoble 07:00 → arrivée
`2026-08-11T00:31:00+02:00`, et non plus datée le jour de départ).

### Tests

```bash
.venv/bin/python -m unittest tests.test_api -v   # 17 tests, OK (1,8 s)
```

Couvre : health, stations/search (dont limit + vide), journeys (canonique §7.4,
par nom, groupe « Paris », marche inter-gares, coordonnées, arrivée jour+1,
count/max_transfers), erreurs 404/400/422 et `journeys: []`.

## 11. Exécution T6 (site web MVP) — 10/08/2026

### Choix de stack

La recommandation §8.1 (Next.js/React) est **différée**. Le MVP est une **SPA
statique** (HTML/CSS/JS vanilla dans `web/`) **servie par FastAPI** : un seul point
d'entrée, pas de CORS, aucun build ni outillage node. Les routes `/v1/*` sont
déclarées avant le montage `StaticFiles` et restent donc prioritaires. Migration
possible vers Next.js/React en T7 si le SEO/SSR le justifie.

### Écrans (§8.2)

- **Recherche** : champs De/À avec autocomplete (`/v1/stations/search`, debounce
  200 ms, clavier/échap), date (bornée par la couverture du graphe via
  `/v1/health`), heure, sens départ/arrivée, correspondances max 0-3.
  Trains uniquement par défaut (`vehicle=train_only`, plus de case dans le
  formulaire depuis le 12/08/2026 — `vehicle=all` reste disponible via l'API).
- **Résultats** : liste Pareto triée par heure de départ (départ, arrivée, durée,
  nb de correspondances, lignes) ; badge **Train TER / Car TER / TramTrain /
  Marche** par leg ; badge « +1j » quand l'arrivée est le lendemain
  (ex. Paris → Grenoble 07:00 → 00:31 +1j).
- **Détail** : timeline gare par gare (heure, ligne, numéro de train), note
  « Ce trajet ne comporte que des TER ».
- Erreurs : 404 gare introuvable affiché avec suggestions ; aucun trajet → message.
- Footer : « Données : SNCF Open Data, licence ODbL » (§12).

### Vérification de bout en bout

```bash
.venv/bin/python -m uvicorn src.api:app --port 8000
# puis dans le navigateur : http://localhost:8000
# 1. « Paris Gare de Lyon » → « Besançon Viotte », 10/08/2026 07:00, départ
# 2. → 1 correspondance : 07:34 → 12:04 (K7 + C11 via Dijon)
# 3. clic → détail timeline (07:34 Paris GDL, 10:33 Dijon, 11:09 Dijon, 12:04 Besançon)
```

### Tests

```bash
.venv/bin/python -m unittest tests.test_api -v   # 21 tests, OK (1,8 s)
```

Couvre T6 : page servie, assets (styles/app.js), attribution ODbL, absence de TGV
affichable — plus les 17 tests T5.

## 12. Prochaines étapes (avant mise en production)

T7 (PWA/mobile, migration possible vers Next.js/React). Le moteur McRAPTOR est
validé par 16 tests unitaires + 16 golden tests ; la parité avec l'oracle est
**16/16** en comparaison train-only, et les marches inter-gares sont une capacité
supplémentaire vérifiée. L'API REST §7 (17 tests) et le site MVP §8 (SPA statique
servie par l'API) sont fonctionnels.
L'API REST §7 est fonctionnelle et testée (17 tests d'intégration).

## 13. Mise en production (big-arm) — 10/08/2026

Le service est maintenant accessible publiquement sur **https://ter.zvz.fr**
(serveur `big-arm`, IP publique `141.253.123.190`).

### 13.1 Service systemd

`/etc/systemd/system/ter-finder.service` — FastAPI/uvicorn sur `127.0.0.1:8000`,
lancé comme l'utilisateur `ubuntu`, restart on-failure, activé au boot :

```ini
[Unit]
Description=TER Finder API + site web (FastAPI/McRAPTOR)
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/ter-finder
ExecStart=/home/ubuntu/ter-finder/.venv/bin/python -m uvicorn src.api:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 13.2 Nginx (reverse proxy + TLS)

`/etc/nginx/sites-enabled/ter.zvz.fr` : redirection 80 → 443, puis proxy vers
`127.0.0.1:8000`. Certificat Let's Encrypt **wildcard** `zvz.fr` (renouvelé via
`certbot-dns-ovh`, credentials `/home/ubuntu/.ovh-api`, authenticator `dns-ovh`
dans `/etc/letsencrypt/renewal/zvz.fr.conf`).

```nginx
server {
    listen 80;
    server_name ter.zvz.fr;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name ter.zvz.fr;
    ssl_certificate /etc/letsencrypt/live/zvz.fr/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/zvz.fr/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 13.3 DNS OVH — sous-domaine `ter`

Ajout via l'API OVH (lib python `ovh`, credentials `dns_ovh_*` dans
`/home/ubuntu/.ovh-api`, mêmes que la validation ACME) :

- **Record A** `ter.zvz.fr → 141.253.123.190` (ttl 0, id `5428509706`),
  suivi d'un `POST /domain/zone/zvz.fr/refresh`.
- Scripts : `feed/scratch/add_ter_zvz.py`, `feed/scratch/list_zvz_dns.py`
  (dans `/home/ubuntu`). Pas d'outil `ovh` CLI séparé : l'API est appelée
  directement avec la lib python.

### 13.4 Vérification de bout en bout

```bash
curl -s https://ter.zvz.fr/v1/health
# {"status":"ok","data_date":"2026-12-19","coverage_start":"2026-08-09","coverage_end":"2026-12-19","stations":3462}
curl -s -o /dev/null -w "%{http_code}\n" https://ter.zvz.fr   # 200
```

### 13.5 Mise à jour des données en production

`scripts/refresh_data.sh` : re-télécharge le GTFS, re-filtre TER, valide,
reconstruit `data/graph.bin` (avec rollback de l'ancien en cas d'échec) puis
redémarre le service — à exécuter avec les droits sudo (systemctl). À passer en
cron plus tard si besoin.

## 14. Exécution T7 v2.1 (PWA) — 10/08/2026

Transformation de la SPA en **application installable** (PWA).

### Fichiers ajoutés (`web/`)

- `manifest.webmanifest` : name/short_name, `display: standalone`, `theme_color`/`background_color` `#1a3a8f`, `start_url: /`, icônes 192/512 (`any` + `maskable`) et `apple-touch-icon.png` (180 px).
- `sw.js` : cache `ter-finder-v1` (shell : `/`, `styles.css`, `app.js`, manifest, icônes) + cache `ter-finder-api-v1` borné à 100 entrées. Stratégies : `network-first` pour les navigations et `/v1/journeys` (fallback cache = résultats récents consultables hors-ligne), `stale-while-revalidate` pour `/v1/health`, `/v1/stations/search` et les assets. `skipWaiting` + `clients.claim` pour une activation immédiate.
- Icônes générées par script PIL (`/tmp/make_icons.py`, texte « TER » sur fond bleu + barre rail).

### Modifications

- `index.html` : `<link rel="manifest">`, `theme-color`, `<link rel="icon">`, `apple-touch-icon`, méta iOS (standalone, barre noire translucide, titre).
- `app.js` : inscription de `/sw.js` au `load` + détection d'une mise à jour du SW.

### Vérification

```bash
curl -s https://ter.zvz.fr/manifest.webmanifest   # 200 application/manifest+json
curl -s https://ter.zvz.fr/sw.js                   # 200 text/javascript
curl -s https://ter.zvz.fr/icon-192.png            # 200 image/png
node --check web/sw.js web/app.js                  # syntaxe OK
.venv/bin/python -m unittest tests.test_api -v    # 21 tests OK (1,8 s)
```

Installable depuis mobile (Chrome/Android, Safari « Ajouter à l'écran d'accueil »). Prochaine sous-tâche T7 v2.2 : app React Native réutilisant l'API T5.

## 15. Exécution T7 v2.2 (app native Capacitor + CI) — 10/08/2026

App Android native réutilisant la SPA web (même codebase), buildée en CI.

### Décisions

- **Capacitor** plutôt que React Native (initialement prévu au PLAN) : la SPA `web/` est enveloppée telle quelle dans un WebView natif. Un seul codebase, pas de duplication des écrans. Même pattern que le repo `muarf/pressscraper`.
- Repo GitHub : **`muarf/PlanTER`** (créé vide précédemment), branché en `origin` du projet.
- On ne compile pas localement : la CI (GitHub Actions) produit l'APK.

### Fichiers ajoutés

- `package.json` / `capacitor.config.json` (`appId fr.zvz.terfinder`, `appName TER Finder`, `webDir: web`).
- `android/` : projet natif généré par `npx cap add android` (commité ; `assets/public` et `capacitor.config.json` régénérés par `npx cap sync`).
- `.github/workflows/build-apk.yml` : checkout → node 22 (`npm install`) → `npx cap sync android` → JDK 21 (temurin, cache gradle) → `./gradlew assembleDebug` → upload artefact `ter-finder-debug-apk`.
- `.gitignore`/`.gitattributes` (données, `.venv`, `node_modules`, build android exclus).

### Adaptation de la SPA

`web/app.js` : `API_BASE` — en WebView Capacitor (`window.Capacitor.isNativePlatform()`), les appels `/v1/*` pointent sur `https://ter.zvz.fr` ; sur le web servi par FastAPI ils restent à la même origine. Le service worker ne cache pas les appels cross-origin (comportement déjà géré).

### Vérification

- `npm install` + `npx cap add android` + `npx cap sync android` : OK sur big-arm.
- CI : run `Build Android APK` **vert** (push + dispatch), artefact APK debug ~3,7 Mo téléchargeable.
- Reste (hors périmètre serveur) : signature release (keystore), icônes/splash finalisées, publication Play/App Store, appId iOS éventuel.

## 16. Exécution T9 (liens Trainline) — 10/08/2026

PoC de monétisation : des liens de réservation Trainline pré-remplis, sans paramètre d'affiliation pour l'instant (le programme d'affiliation lui-même reste à étudier).

### Découverte : format des liens et cartographie

- L'URL « deep link » fonctionnelle est `https://www.thetrainline.com/book/results?origin={code}&destination={code}&outbound_date={YYYY-MM-DD}[&outbound_time={HH:MM}]` (les URL `trainline.com/search/{slug}-to-{slug}/on/…` renvoient 404). L'API de recherche est chargée côté client, pas de validation HTTP possible sans navigateur.
- Cartographie UIC → **slug Trainline** : le repo public **`trainline-eu/stations-studio`** publie `public/stations.csv` (id, slug, `uic8_sncf`, `sncf_id` FR…). Téléchargé puis réduit aux gares FR (`config/trainline_stations.csv`, 4456 lignes, 4000 UIC uniques).
- **Format du code validé le 11/08/2026** (navigateur headless Chromium sur big-arm, inspection du DOM rendu) : seul le **slug** est accepté par `/book/results` (`origin=dijon-ville&destination=besancon-viotte` → gares résolues). Les codes `sncf_id` (FRPLY…) déclenchent « Le code d'emplacement de la gare de départ doit être indiqué » et les ids numériques (38, 45…) ne résolvent aucune gare.

### Implémentation

- `src/trainline.py` : chargement CSV (cache), `slug_for(stop_area_id)` (accepte `StopArea:OCE…` / `OCE…`), `booking_url(from, to, date, time)`.
- `src/api.py` : `trainline_slug` ajouté aux réponses `/v1/stations/search` ; objet `booking.{provider,url}` par **leg ferroviaire** dans `/v1/journeys` (trajets à correspondances décomposés — un billet TER par segment, marches exclues), ex. Paris → Besançon : `origin=paris-gare-de-lyon&destination=dijon-ville&outbound_date=2026-08-12&outbound_time=07:34` puis `origin=dijon-ville&destination=besancon-viotte&outbound_date=2026-08-12&outbound_time=11:09`.
- `web/app.js` + `web/styles.css` : bouton « Voir les horaires & acheter sur Trainline » sur l'écran détail (lien `_blank`, classe `.buy-link`), affiché uniquement quand `j.booking.url` existe.

### Vérification

```bash
curl -s "https://ter.zvz.fr/v1/journeys?from=OCE87686006&to=OCE87718007&date=2026-08-10&time=07:00&count=1"
# booking per leg: {provider: trainline, url: https://www.thetrainline.com/book/results?origin=paris-gare-de-lyon&destination=dijon-ville&outbound_date=2026-08-12&outbound_time=07:34}
.venv/bin/python -m unittest tests.test_api -v   # 21 tests OK
```

Gares non mappées (peu fréquentes) : `booking.url` vaut `null`, pas de bouton.

## 17. Versionnement GitHub + état du dépôt — 10/08/2026

- Le projet entier est maintenant dans le repo **`muarf/PlanTER`** (branch `main`, commit initial « TER Finder T1-T7 + T9 »).
- `data/`, `.venv/`, `node_modules/`, build android sont gitignorés (régénérables).
- Les données et l'API restent déployées sur `big-arm` ; le serveur de prod n'est pas géré par la CI (le workflow ne build que l'APK).

## 18. Prochaines étapes (mise à jour)

**État au 12/08/2026.** Toutes les tâches techniques T1–T8, T10 et T11 sont livrées et validées (moteur, API, web, PWA/native, temps réel, alerting, cartes TER). Restent, par ordre d'opportunité :

1. **Publication stores (§9/Phase 4)** — l'app native Capacitor (T7 v2.2) n'est **pas publiée** sur Play Store / App Store : nécessite comptes développeur (25 € une fois Google, 99 €/an Apple) + soumission manuelle. Non bloquant.
2. **Surveillance opérationnelle** — **Livré le 12/08/2026** (T10).
3. **Programme d'affiliation (T9/Phase 6)** — le PoC (liens Trainline + cartes TER T11) est livré ; le programme proprement dit (commission, paramètre d'affiliation, tracking) dépend d'un accord commercial, hors code.

Les déploiements web/API sont documentés en §13.

## 19. Exécution T8 (temps réel GTFS-RT) — 11/08/2026

T8 livrée : retards et suppressions SNCF intégrés au calcul d'itinéraires, via le
flux GTFS-RT Trip Updates.

### Flux et mapping

- URL : `https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates`
  (maj toutes les ~2 min, horizon ~60 min). 833 Ko protobuf, ~765 trips TER mappés
  exactement sur les trips du graphe (via `Graph.trip_index`, `trip_id` au même format).
- `stop_id` du flux `StopPoint:OCETrain TER-<uic8>` → `StopArea:OCE<uic8>` :
  **100 % des stops** mappés via `graph.stop_index`.
- Décisions : retard ≥ 0 uniquement (un train en avance n'est jamais avancé) ;
  délai max arr/dep par arrêt ; suppression via `TripDescriptor.CANCELED` ;
  flux obsolète après ~6 min ; échec de fetch = l'état précédent est conservé.

### Implémentation

- `src/gtfs_rt.py` : `parse_trip_updates` (décodage protobuf → `RealtimeFeed`),
  `RealtimePoller` (thread daemon, intervalle 120 s, verrou + `snapshot()`).
- `src/raptor.py` : `_views` applique retards/cancels (cache contourné quand
  `realtime` est fourni) ; `depart_after`/`arrive_by`/miroir propagent `realtime` ;
  `_shift_leg` décale les horaires réels d'un leg (départ = retard à
  l'embarquement, arrivée = retard au débarquement) et expose `Leg.delay_min`.
- `src/api.py` : poller démarré avec le moteur (lifespan), paramètre
  `use_realtime` sur `/v1/journeys` (défaut `true` : les retards réels sont
  toujours appliqués, le paramètre reste pour compatibilité descendante),
  section `realtime` dans `/v1/health`
  (âge, fraîcheur, nb trips retardés/supprimés, timestamp GTFS-RT), alerte
  `connection_risks` (jonction dont le retard a consommé la marge planifiée).
- UI : badges `+X min` dans
  résultats et détail, avertissement « correspondance risquée » + bouton
  « Voir une alternative plus tard (+30 min) ».

### Vérification (prod)

```bash
curl -s https://ter.zvz.fr/v1/health | python3 -m json.tool   # realtime: {age_s, delayed_trips, cancelled_trips, fresh}
curl -s "https://ter.zvz.fr/v1/journeys?from=Paris+Est&to=Strasbourg&date=2026-08-11&time=05:00&use_realtime=true&count=3"
# leg K1 affiché avec delay_min: 20 ; un train supprimé ne génère plus d'itinéraire direct
.venv/bin/python -m unittest tests.test_gtfs_rt tests.test_raptor tests.test_api -v  # 59 tests OK
```

Redéploiement : `systemctl restart ter-finder.service` (deps venv déjà à jour :
`gtfs-realtime-bindings 2.2.0`, `protobuf 7.35.1`), contrôle du vrai redémarrage
via `ActiveEnterTimestamp` puis du premier fetch GTFS-RT.

## 20. Exécution T8 — Service Alerts (perturbations) — 11/08/2026

Complément T8 : le flux GTFS-RT **Service Alerts** (`sncf-gtfs-rt-service-alerts`)
est intégré et affiché sous forme de bandeau de perturbation. Boucle le §10 du PLAN.

### Flux et mapping

- URL : `https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-service-alerts`
  (~837 Ko, 348 entités sur le premier contrôle : 322 `only_trip`, 24 `both`,
  2 `only_stop` ; causes MAINTENANCE 112, UNKNOWN_CAUSE 133, OTHER_CAUSE 103).
- Deux cibles possibles dans `informed_entity` :
  - `stop_id` = `StopArea:OCE<uic8>` → mappé directement sur `graph.stop_index`
    (126 gares concernées, 100 % couvertes).
  - `trip_id` = `OCESN17810F` (**numéro de train court**, sans date) → les trips
    du graphe sont `OCESN117760F1187_F:…` ; mapping **par numéro de train** via
    la regex `OCES(N?\d+)F`. 5860/6015 numéros d'alertes retrouvés dans les trips
    du graphe (un numéro couvre plusieurs dates : même circulation quotidienne).
- Période d'activité (`active_period`) : une alerte non bornée ou dont la période
  englobe maintenant est active ; hors période = ignorée.
- Décision de filtrage : alerte **générale** (aucune cible exploitable) → exclue
  du bandeau par trajet (trop nombreuses, non ciblables — ex. guichets fermés,
  infos confort), mais **comptée** dans `/v1/health` ; alerte par **gare** →
  si le trajet y passe ; par **numéro de train** → si un leg ferroviaire du
  trajet l'utilise.

### Implémentation

- `src/gtfs_rt.py` : `RealtimeAlert` (id, header, description tronquée à 200
  car., cause, effect, cibles `stops`/`train_numbers`, `general`, `to_json()`),
  `RealtimeAlerts` (`age_s()`, `snapshot()`, `relevant(stop_idxs, train_numbers,
  include_general=False)`), `parse_service_alerts` (période + cibles),
  `fetch_service_alerts` ; le poller fetch les **deux** flux indépendamment
  (`alerts` et `feed`, chacun avec son log et sa conservation d'état).
- `src/api.py` : champ `alerts` par trajet dans `/v1/journeys` (au plus 3,
  calculé depuis les gares et numéros de train des legs ferroviaires) + section
  `realtime.alerts` dans `/v1/health` (`count`, `fresh`, `age_s`).
- UI : bandeau `⚠ Perturbation signalée : <titre>` dans résultats et détail,
  bouton `Détail`/`Masquer` (élément `span` à l'écoute via délégation, pour ne
  pas imbriquer un `<button>` dans le bouton de trajet).
- Tests : 8 nouveaux cas Service Alerts (cible train/gare, générale, périodes
  active/terminée/à venir, entrée sans header, pertinence croisée gare×train).

### Vérification (prod)

```bash
curl -s https://ter.zvz.fr/v1/health | python3 -m json.tool
# realtime.alerts: {count: 350, fresh: true, age_s: 1}
curl -s "https://ter.zvz.fr/v1/journeys?from=Dijon&to=Lyon+Part+Dieu&date=2026-08-11&time=08:00&use_realtime=true&count=5"
# leg K7 08:49 → alerts: [Info Travaux (MAINTENANCE), Affluence été]
.venv/bin/python -m unittest tests.test_gtfs_rt tests.test_raptor tests.test_api tests.golden_tests -v  # 59 tests OK
```

Commit `5302a2a`, déployé et vérifié en prod le 11/08/2026.

## 21. Correctif T8 — retards datés par date de service (11/08/2026)

### Symptôme

Une recherche `use_realtime=true` affichait un retard sur un train **dans deux
jours** (ex. « +10 min » à J+2). Impossible a priori : un flux temps réel
n'annonce que les trains qui circulent maintenant.

### Cause racine

- Le flux `sncf-gtfs-rt-trip-updates` annonce le retard d'une **course précise**,
  identifiée par `TripDescriptor.start_date` (date de service réelle, YYYYMMDD —
  observé : **976/976** entités TER avec `start_date`).
- Le suffixe daté du `trip_id` SNCF (NewTripId, ex. `…:20261211`) **n'est pas la
  date de service** : un même `trip_id` circule sur plusieurs jours (vérifié :
  15 dates pour un trip, dont aujourd'hui **et** J+2).
- Le parsing rangeait les retards **uniquement par `trip_id`** → le retard du jour
  (start_date = aujourd'hui) s'appliquait à **toutes** les dates où le trip
  circule, y compris J+2 et au-delà.

### Correctif

- `src/gtfs_rt.py` : `RealtimeFeed.trip_delays` et `.cancelled` indexés par
  **`(trip_id, date_ymd)`** ; date dérivée de `TripDescriptor.start_date`, en
  repli de l'horaire absolu (`time`) le plus précoce en Europe/Paris, sinon du
  header du flux ; entité indatable ignorée (elle ne doit pas s'appliquer à
  toutes les dates).
- `src/raptor.py` : `_views` consulte `(trip.id, date)` pour retards et
  suppressions ; `date` propagé dans `_pareto_journeys`/`_reconstruct` (le
  décalage des horaires d'un leg est lui aussi daté).
- Conséquence produit : un retard n'est affiché que **le jour de sa date de
  service** — pas de prédiction de retard futur, pas de fuite entre dates.

### Vérification

- Feed réel : 208 retards + 8 suppressions, **100 % datés au 11/08/2026**
  (avant correctif, les clés mêlaient des dates jusqu'en décembre).
- Même train Lyon → Paris Bercy (trip `…:20260816`, service du 10 au 16/08) :
  - 11/08 : départ 15:21, `delay_min: 5` (horaire réel décalé) ;
  - 13/08 (J+2) : départ 15:16 (théorique), `delay_min: 0` — plus de retard.
- Suite complète : **60 tests OK**. Commit `5061018`, déployé en prod (restart
  `ter-finder.service`) ; `/v1/health` : `delayed_trips: 206, cancelled_trips: 8`.

## 22. CI — correction du job release (11/08/2026)

Le commit `4a56700` (AAB release signé Play Store) cassait le workflow : le `if`
du job `release` référençait `secrets.KEYSTORE_BASE64`, **interdit par GitHub
Actions dans une condition de job** → échec à 0 s de tous les runs (message
générique « workflow file issue »). Correctif `.github/workflows/build-apk.yml` :
le job `release` s'exécute toujours (sauf pull_request), une étape
`Check keystore availability` lit `KEYSTORE_BASE64` via `env` et exporte
`skip=true` si le secret est absent ; les étapes de décodage/signature/upload ne
tournent que si `skip != true`. CI verte (APK debug buildé + release si secrets).

## 23. Exécution T10 (surveillance opérationnelle et alerting Telegram) — 12/08/2026

Intégration d'un système d'alerting opérationnel complet sur Telegram.

### Configuration du Bot & Chat ID
- Utilisation d'un bot Telegram (`PlanTER`) ; le token et le chat ID ne sont **pas** commités : ils sont lus depuis `/etc/ter-finder/ter-finder.env` (variables `TER_FINDER_TELEGRAM_TOKEN` / `TER_FINDER_TELEGRAM_CHAT_ID`, voir `.env.example`). Sans ces variables, les alertes sont silencieusement ignorées.

### Implémentation
- **Alerte sur échec du pipeline** : Modifications de `scripts/refresh_data.sh` pour envoyer une alerte en cas d'erreur de téléchargement, filtrage, validation, build ou redémarrage du service systemd.
- **Surveillance continue** : Script `scripts/monitor_health.py` effectuant des requêtes locales vers `/v1/health`. Il détecte :
  - L'API injoignable ou en code d'erreur HTTP non-200.
  - La perte de fraîcheur du flux temps réel (Trip Updates / Service Alerts).
  - L'expiration imminente des données GTFS théoriques (dans les 7 jours).
  - Un échec du refresh hebdomadaire.
  - *Anti-spam* : Fichier de verrou `/tmp/ter_finder_alert_sent` permettant de n'envoyer qu'un seul message par incident persistant, et un message de rétablissement `✅` lors du retour à la normale.
- **Automatisation** : Planification automatique via cron (`crontab -e`) toutes les 10 minutes pour l'utilisateur `ubuntu`, en sourçant `/etc/ter-finder/ter-finder.env` avant le script. Le service systemd `ter-finder-refresh.service` charge le même fichier via `EnvironmentFile=`. Le token a été retiré des scripts le 12/08/2026 (sécurité : plus aucun secret en clair dans le repo).

### Vérification
- Validation de l'envoi de l'alerte de test via curl (reçue avec succès sur Telegram).
- Exécution manuelle réussie de `scripts/monitor_health.py` (aucune erreur, API saine).

## 24. Exécution T11 (cartes de réduction TER) — 12/08/2026

Les cartes de réduction TER (Carte solidaire, abonnements régionaux…) ont d'abord été exposées dans le web et appliquées au **lien de réservation trajet total** vers Trainline. **Le champ web a été retiré le 12/08/2026** : Trainline n'applique pas la carte via l'URL (voir « Limite » ci-dessous). La logique serveur (`/v1/cards`, param `cards=`, `total_url`) est conservée pour plus tard.

### Rétro-ingénierie : API des cartes Trainline

- `GET https://www.thetrainline.com/api/discount-cards` renvoie 401 sans headers JS, mais **200** avec :
  `x-app-version: 4.48.32605`, `x-client-name: DesktopWeb`, `x-platform-type: web`, `x-api-managedgroupname: TRAINLINE`, `x-version`, `Accept-Language: fr-FR`.
- Résultat : 219 cartes dont 46 `displayGroup=sncf_regional` (cartes TER régionales) → `config/trainline_cards.json` (id hash 40-hex, name, shortName, ageRange optionnel). Ex. la **Carte Bourgogne-Franche-Comté tarif réduit solidaire** = `2a730e22c0be4cf0030f89205f540fe39e8dca6b`, sans `ageRange`.
- Le lien de réservation fonctionne avec **ou sans** `selectedOutward` mais exige `passengerDiscountCards[]={id}` + `passengers[]={DOB}|pid-0` dès qu'une carte est choisie (Trainline calcule l'âge). DOB par défaut : `1993-08-12`.

### Limite découverte le 12/08/2026 : la carte n'est pas appliquée via l'URL

Test réseau (browser headless) sur les deux liens (avec et sans `selectedOutward`) :
- `POST /api/journey-search/` part avec `"cardIds":[]` — le paramètre d'URL `passengerDiscountCards[]` **n'est pas transmis à l'API** pour un trajet FR.
- Le JS de Trainline (`Results-TicketOptions-TicketOptionsV2`) : pour l'Europe (`isEurope`), il envoie `storedDiscountCards` (lu depuis le **storage du navigateur**) au lieu du param d'URL.
- Confirmation utilisateur : le lien « qui marchait » ne s'applique **pas** en navigation privée (session vierge) → la carte ne s'applique que si elle est déjà dans le storage de la session Trainline. Aucun lien ne peut la déclencher (cross-origin : impossible d'écrire dans le localStorage de `trainline.com`).

Conclusion : le champ cartes a été retiré de l'UI ; le lien total pré-remplit le trajet, l'utilisateur ajoute sa carte en un clic dans Trainline.

### Implémentation (conservée)

- `src/trainline_cards.py` : `cards()`, `card_by_id()`, `valid_ids()` (filtre cartes connues, sans doublons), `booking_url(base_url, card_ids)` (ajoute les cartes + `passengers[]`, inchangée sans carte), `DEFAULT_PASSENGER_DOB`.
- `src/api.py` :
  - `GET /v1/cards` → les 46 cartes TER.
  - `/v1/journeys` : paramètre `cards=id1,id2…` ; ajoute `booking.total_url` (gare de départ → gare d'arrivée du trajet, date/heure du premier leg ferroviaire, cartes appliquées via `trainline_cards.booking_url`). Les liens par leg restent inchangés.
- `web/app.js` : chip **« Réserver le trajet (Trainline) »** (total) qui précède les billets par leg (`.ticket-total`). Le champ cartes et `initCardsMenu()` ont été retirés (pas d'envoi de `cards=`).

### Vérification

```bash
curl -s https://ter.zvz.fr/v1/cards | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["cards"]))'   # 46
curl -s "https://ter.zvz.fr/v1/journeys?from=Dijon&to=Besançon Viotte&date=2026-08-10&time=07:00&cards=2a730e22c0be4cf0030f89205f540fe39e8dca6b" | python3 -m json.tool
# booking.total_url contient passengerDiscountCards[]=2a730e22…&passengers[]=1993-08-12|pid-0
# (backend uniquement ; le web ne l'utilise plus)
.venv/bin/python -m unittest tests.test_api tests.test_trainline_cards   # OK
```

## 25. Retrait de la case « Trains uniquement » — 12/08/2026

Comme pour la case « Temps réel » (T8), la case à cocher du formulaire a été
supprimée et le comportement devient le **défaut** :

- `web/index.html` : case retirée.
- `web/app.js` : le paramètre `vehicle` n'est plus envoyé par le web.
- `src/api.py` : `vehicle` par défaut passe de `all` à `train_only` (les cars
  TER ne sont plus proposés par défaut). La valeur `all` reste acceptée par
  l'API pour les clients qui veulent inclure les cars.
- Test : `test_page_daccueil_servie` vérifie l'absence de `name="vehicle"` et
  de « Trains uniquement » dans le HTML ; `test_vehicle_defaut_train_only`
  vérifie que sans paramètre, tous les legs non-marche sont des trains.


## 26. Correctif T3bis — départs masqués par le balayage RAPTOR « large » (13/08/2026)

**Symptôme utilisateur** : recherche Saint-Vit → Paris-Bercy à 11:00 — le
départ 12:17 n'apparaissait pas, la liste passait directement à 14:06.

**Cause** : RAPTOR simple ne garde que l'arrivée la plus précoce par round.
Le 12:17 Saint-Vit (→ Dijon 13:02) rejoint exactement le **même K7 N17758**
(13:32) que le 11:06 → même arrivée 17:06 → dominé et jeté. Le balayage large
par tranches de 3 h (`slice_min=180`) ne sauvait rien : les deux départs
tombent dans la même tranche 11:00.

**Correctif** (`src/raptor.py`) :
- nouveau cœur commun `_sweep_wide` (tranches fixes + **révélation**) utilisé
  par `depart_after_wide` ;
- la révélation relance le balayage au « départ du trajet trouvé + 1 » pour
  chaque trajet découvert : la chaîne énumère tous les départs utiles de
  l'horizon (bornée par `MAX_REVEAL_PASSES=40`), y compris ceux qui
  rattrapent la même correspondance qu'un départ précédent ;
- effet de bord positif : Dijon → Besançon Viotte affiche désormais tous les
  directs horaires (07:09, 07:40, 08:08, 09:09, 10:12…) au lieu d'un seul par
  tranche de 3 h ;
- la révélation est **désactivée pour ArriveBy** (`reveal=False`) : elle ne
  ferait apparaître que des départs plus tôt / arrivées plus tôt que la
  meilleure option « départ le plus tardif », sans valeur ajoutée ;
- tests : 4 nouveaux (`tests/test_raptor.py` ×3, `tests/test_api.py` ×1) ;
  suite complète **78 tests OK**.



