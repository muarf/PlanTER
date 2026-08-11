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
  `/v1/health`), heure, sens départ/arrivée, correspondances max 0-3,
  filtre « Trains uniquement » (`vehicle=train_only`).
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
- Cartographie UIC → code : le repo public **`trainline-eu/stations-studio`** publie `public/stations.csv` (id, slug, `uic8_sncf`, `sncf_id` FR…). Téléchargé puis réduit aux gares FR avec `sncf_id` (`config/trainline_stations.csv`, 4456 lignes, 4000 UIC uniques).

### Implémentation

- `src/trainline.py` : chargement CSV (cache), `code_for(stop_area_id)` (accepte `StopArea:OCE…` / `OCE…`), `booking_url(from, to, date, time)`.
- `src/api.py` : `trainline_code` ajouté aux réponses `/v1/stations/search` ; objet `booking.{provider,url}` par trajet dans `/v1/journeys` (premier/dernier leg **non-marche**, ex. Paris → Besançon : `origin=FRPLY&destination=FRABG&outbound_date=2026-08-10&outbound_time=07:34`).
- `web/app.js` + `web/styles.css` : bouton « Voir les horaires & acheter sur Trainline » sur l'écran détail (lien `_blank`, classe `.buy-link`), affiché uniquement quand `j.booking.url` existe.

### Vérification

```bash
curl -s "https://ter.zvz.fr/v1/journeys?from=OCE87686006&to=OCE87718007&date=2026-08-10&time=07:00&count=1"
# booking: {provider: trainline, url: https://www.thetrainline.com/book/results?origin=FRPLY&destination=FRABG&outbound_date=2026-08-10&outbound_time=07:34}
.venv/bin/python -m unittest tests.test_api -v   # 21 tests OK
```

Gares non mappées (peu fréquentes) : `booking.url` vaut `null`, pas de bouton.

## 17. Versionnement GitHub + état du dépôt — 10/08/2026

- Le projet entier est maintenant dans le repo **`muarf/PlanTER`** (branch `main`, commit initial « TER Finder T1-T7 + T9 »).
- `data/`, `.venv/`, `node_modules/`, build android sont gitignorés (régénérables).
- Les données et l'API restent déployées sur `big-arm` ; le serveur de prod n'est pas géré par la CI (le workflow ne build que l'APK).

## 18. Prochaines étapes (mise à jour)

Phase 3 (T6 web) livrée et en ligne. Prochaine : **T7** (PWA puis mobile natif).
Les déploiements web/API sont documentés en §13.
