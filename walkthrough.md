# PlanTER — Walkthrough T1

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
Description=PlanTER API + site web (FastAPI/McRAPTOR)
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

- `package.json` / `capacitor.config.json` (`appId fr.zvz.terfinder`, `appName PlanTER`, `webDir: web`).
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

1. **Calculs Tarifaires et Réductions (T12/Phase 8)** — Implémenter l'estimation du tarif plein et le calcul des tarifs réduits par carte régionale via un moteur de calcul interne autonome (calculs sur distance globale et par segments).
2. **Publication stores (§9/Phase 4)** — l'app native Capacitor (T7 v2.2) n'est **pas publiée** sur Play Store / App Store : nécessite comptes développeur (25 € une fois Google, 99 €/an Apple) + soumission manuelle. Non bloquant.
3. **Surveillance opérationnelle** — **Livré le 12/08/2026** (T10).
4. **Programme d'affiliation (T9/Phase 6)** — le PoC (liens Trainline + cartes TER T11) est livré ; le programme proprement dit (commission, paramètre d'affiliation, tracking) dépend d'un accord commercial, hors code.

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

## 24. Modélisation de la Tarification TER (Découverte et Analyse) — 12/08/2026

Analyse comportementale de la tarification régionale des TER sur des trajets avec correspondance.

### Constatations clés
1. **La dégressivité sur parcours global** : Sur un trajet mono-régional (ex: `Paris ➔ Dijon ➔ Besançon`, géré de bout en bout par la région Bourgogne-Franche-Comté), le prix est calculé sur la distance totale de 405 km en appliquant la dégressivité kilométrique. Le prix du billet unique est de **41,00 €** (alors que la somme des billets pris séparément est de 63,60 €).
2. **La somme par rupture de convention interrégionale** : Sur un trajet pluri-régional sans convention spécifique (ex: `Lille ➔ Amiens ➔ Rouen`), la correspondance à Amiens fait passer de la région Hauts-de-France à la région Normandie. Le prix final facturé (notamment constaté à **43,00 €** sur Trainline) est la somme des deux segments régionaux séparés (22,10 € + 20,30 €).

### Impacts pour l'architecture PlanTER
Toute future fonctionnalité de tarification ne pourra pas se baser sur une simple addition des segments de voyage. L'algorithme devra déterminer les régions organisatrices de chaque étape pour décider si le barème dégressif s'applique globalement sur la distance totale cumulée ou si les tarifs par zone/région doivent être sommés de manière disjointe.

## 25. Exécution T11 (cartes de réduction TER) — 12/08/2026

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

## 26. Retrait de la case « Trains uniquement » — 12/08/2026

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


## 27. Correctif T3bis — départs masqués par le balayage RAPTOR « large » (13/08/2026)

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



## 28. MVP Tarification (T12) — prix estimés, branche pricing (13/08/2026)

**Contexte.** Analyse de la tarification TER (§24) : les régions organisatrices
appliquent un barème dégressif sur la distance totale pour les trajets
mono-région (un billet unique), et la somme des billets par tronçon sinon
(pluri-région, sans accord tarifaire inter-régions).

**MVP livré sur la branche `pricing`** (déployée sur https://betater.zvz.fr) :

- `scripts/build_station_regions.py` : télécharge SNCF Open Data
  « liste-des-gares » (code UIC → libellé/département), convertit le
  département en région administrative (mapping INSEE) et écrit
  `config/station_regions.json`. Couverture du graphe : 2545/3462 gares (73,5 %,
  inconnues = gares frontalières principalement).
- `src/pricing.py` (`PricingEngine`) :
  - distance d'un leg = somme des haversine entre arrêts × 1,17
    (≈ longueur de voie) ;
  - région d'un train = région majoritaire de ses arrêts ;
  - tarif = `scale_région × (a·√km + b·km)`, arrondi aux 5 centimes, plancher
    3 € (config `config/pricing.yaml`) ;
  - règle mono/pluri-région appliquée par trajet.
- `src/api.py` : `/v1/journeys` expose `price_normal_eur` + `pricing`
  (rule, km, régions, legs) — toujours marqué comme **estimation**.
- Web : `priceChip` ≈ prix dans les résultats et le détail (bloc
  `priceBlock` : km/région par leg + règle appliquée).

**Calibration (prix observés Trainline le 12/08/2026)** : BFC 405 km →
41,00 € (scale 0,9865) ; HDF 112 km → 22,10 € (scale 1,0425) ; Normandie
112 km → 20,30 € (scale 0,9575).

**Résultats de référence sur le graphe réel :**
- Dijon → Besançon Viotte (07:09) : 102,9 km mono-région BFC → **20,00 €**
  (réel ≈ 12 € — limite connue du modèle sur les trajets courts) ;
- Saint-Vit → Paris Bercy : C11+K7, 399 km mono-région BFC → **41,25 €**.

**Limites à affiner avant d'aller plus loin :** très peu d'observations pour
calibrer (3 prix), distance approximative, prix courts surévalués, cartes de
réduction non appliquées (`price_reduced_eur` à venir), gares frontalières
sans région.
## 29. T12 — Cartes de réduction : price_reduced_eur (13/08/2026)

**Contexte.** Le lien Trainline ne peut pas appliquer les cartes de réduction
(§25 : `cardIds` non transmis, storage cross-origin inaccessible). Le moteur
interne de T12 s'en charge : il calcule lui-même le tarif réduit.

**Modèle (`config/pricing.yaml`, section `cards`).** Chaque carte TER de
`config/trainline_cards.json` (46 cartes) se voit attribuer :
- `pay` = fraction du plein tarif PAYÉE (0.50 = -50 %) — taux REPRÉSENTATIF
  car les barèmes réels varient (semaine vs week-end, heures, ressources) ;
- une région d'application déduite du nom (keywords) ;
- `no_discount_prefixes` (abonnement/pass/forfait/sûreté) : pas de réduction
  par billet unitaire (voyages illimités ou tarif fixe) ;
- motifs par ordre de priorité + dérogations `by_id`.

**Barèmes publics utilisés (recherches 13/08/2026) :**
- BFC « TRAIN Mobigo+ » 26+ : 60 % we / 30 % semaine → 0,40 ; Jeune -26 : 50 % ;
  solidaire : 75 % → 0,25 ;
- Hauts-de-France Ma Carte TER / -26 : 50 % → 0,50 ;
- PDL mezzo/mezzo-26 : 50 % ; mobi 50 % → 0,50 ; mobi 75 % → 0,25 ;
- ARA illico LIBERTÉ : -25 % sem / -50 % we → 0,50 ; illico JEUNES : 0,50 ;
  illico MOBILITÉ : -90 % → 0,10 ;
- Occitanie LibertiO' : 50 % we / 30 % sem → 0,40 ;
- Grand Est Fluo / Fluo Jeune : 50 % ; Solidaire : -80 % → 0,20 ;
- Région Sud : ZOU! Malin -30 % (0,70), Solidaire -50 % (0,50), Solidaire + -90 %
  (0,10), Études -50 % (0,50) ;
- Normandie Tempo : -25 % sem / -50 % we → 0,50 (Carte +26 et Tempo Paris +26) ;
- Nouvelle-Aquitaine Carte + : 50 %, Solidaire : -80 % → 0,20 ; Centre-Val Rémi : 50 % ;
- Bretagne BreizhGo Solidaire : tarifs fixes (jusqu'à -75 %), représentatif 0,50 ;
  Grand Est Primo (abonnement illimité) : sans réduction (type none).

**Règle d'application.** Une carte ne réduit QUE les segments de sa région :
- mono-région : réduction sur le billet global dégressif ;
- pluri-région : réduction du billet de tronçon de sa région, les autres
  tronçons restent au plein tarif ;
- plusieurs cartes : la plus avantageuse par région (min de `pay`) ;
- carte hors région du trajet : sans effet (non listée dans `pricing.cards`).

**API.** `/v1/journeys?cards=id,…` (ids validés par `trainline_cards.valid_ids`)
expose `price_reduced_eur` (== `price_normal_eur` sans carte) et
`pricing.cards` (id, name, shortName, region, pay des cartes appliquées).

**Web.** Sélecteur de carte de réduction (`<select>` alimenté par `/v1/cards`)
réintégré dans le formulaire ; chip de résultat barrant le plein tarif quand la
réduction s'applique ; bloc détail avec tarif réduit et cartes appliquées.

**Vérifs (graphe réel) :**
- Dijon → Besançon Viotte : 20,00 € plein tarif ; carte solidaire BFC (-75 %)
  → 5,00 € ; avec 26+ (0,40) → 8,00 € ; les deux → 5,00 € ;
- Lille → Rouen via Amiens : 45,25 € ; Ma Carte TER HdF → 33,20 € (seul le
  tronçon HdF passe à 50 %, le tronçon normand reste plein tarif).

**Limites :** taux représentatifs (pas la grille semaine/week-end réelle), pas
de gestion de l'âge ni des accompagnants, cartes nationales (Avantage/Liberté)
non couvertes (la liste est `sncf_regional`), 100 tests OK.

## 30. T12 — Web : page « comment obtenir chaque carte de réduction TER » (13/08/2026)

**Contexte.** Les cartes de réduction TER (base `config/trainline_cards.json`,
46 cartes) ne servaient pas encore à l'utilisateur final : le champ web avait
été retiré du formulaire (§25) car Trainline n'applique pas la carte via
l'URL. La page `web/cards.html` rend ces cartes consultables : prix,
réduction, conditions, démarche et lien officiel.

**Page (`web/cards.html`).**
- Menu de navigation par région (11 régions : ARA, BFC, Bretagne, Centre-Val
  de Loire, Grand Est, Hauts-de-France, Normandie, Nouvelle-Aquitaine,
  Occitanie, Pays de la Loire, PACA/Région Sud).
- 54 cartes/offres réparties par région (les 46 cartes du fichier
  `trainline_cards.json` + offres régionales type packs/pass), chacune avec
  « Prix », « Réduction », « Conditions », « Démarche » et « Lien » (site
  officiel du réseau).
- Sélecteur multi-régions (filtre des cartes affichées).

**Cartes ajoutées le 13/08/2026 (vérifiées sur la beta betater.zvz.fr) :**
- Hauts-de-France : Pack TER Chantilly (27 €/adulte, AR TER + entrée 1 jour
  au Domaine de Chantilly) ;
- Occitanie : Compte mobilité liO `+=0` (12-26 ans), `+=Flex` (27-59 ans),
  `+=-` (60 ans et +) — dégressivité −10 % à −90 % selon le nombre de trajets,
  plafonds 97 €/mois en train et 44 €/mois en car, gratuit (Fairtiq) ;
- Pays de la Loire : Forfait multi (35 € 1 jour / 50 € 2 jours, 1-5 pers.) ;
- Région Sud : Pass train ZOU! (journée 20-30 €, régional 60 €/3 j ou
  100 €/7 j, +5 €/accompagnant).

**Vérification beta (13/08/2026).** Parcours de toutes les cartes régionales
sur https://betater.zvz.fr/cards.html : 11 sections régionales affichées,
liens officiels corrects, nouvelles cartes présentes. Le service beta
(`ter-finder-pricing.service`, port 8001) sert la branche `pricing`.

**Mise à jour prod.** `web/cards.html` est servi statiquement ; prod
(`ter-finder.service`, port 8000, ter.zvz.fr) est mise à jour par `git pull`
puis redémarrage du service.

## 31. T12 — Vérification des pourcentages de réduction sur la recherche d'itinéraire (13/08/2026)

**Contexte.** Les taux représentatifs des cartes (§29) sont exposés à l'utilisateur
de deux manières : page « comment obtenir chaque carte » (§30, `web/cards.html`)
et recherche d'itinéraire (sélecteur de carte + `price_reduced_eur`).

**Vérification (beta, 13/08/2026).** Parcours de la recherche d'itinéraire sur
https://betater.zvz.fr : les pourcentages de réduction affichés correspondent
aux taux du modèle `config/pricing.yaml` et à ceux de la page cartes.

Le champ `discount_pct` de l'API `/v1/cards` est dérivé de `pay` :
`discount_pct = (1 − pay) × 100`. Exemples relevés :
- ARA : illico SOLIDAIRE −75 %, illico MOBILITÉ −90 %, illico LIBERTÉ −50 %
  (cohérent avec cards.html) ;
- Occitanie : SolidariO' −75 %, LibertiO' −50 % ;
- PDL : mobi 75 % → −75 %, mobi 50 %/mezzo → −50 % ;
- Région Sud : ZOU! Solidaire + −90 %, ZOU! Malin −30 %, ZOU! −26 −50 % ;
- BFC : Tarif réduit solidaire −75 %, Mobigo+ 26+ −60 % ;
- HdF/Normandie/Grand Est/NA/CVL : −50 % (Ma Carte TER, Tempo, Fluo, Carte +, Rémi).

Ces valeurs s'appliquent au calcul de `price_reduced_eur` dans `/v1/journeys`
(uniquement sur les segments de la région de la carte, §29).

**Correction (13/08/2026).** Contrôle carte par carte cards.html ↔ recherche : le

modèle appliquait des taux erronés sur illico MOBILITÉ (−90 % vs −50 %),

Solidaires Grand Est et Nouvelle-Aquitaine (−80 % vs −50 %), Tempo +26 et

Tempo Paris +26 (−60 % vs −25/−50 %), illico LIBERTÉ (−60 % vs −25/−50 %),

ZOU! Études (−50 % vs aucune) et Primo (aucune vs −50 %). `config/pricing.yaml`

mis à jour pour être cohérent avec cards.html ; vérifié sur la beta.

## 32. T12 — Découpage d'un billet intra-train multi-régions (13/08/2026)

**Contexte.** Un même TER peut traverser plusieurs régions (ex. K7 Paris Gare de
Lyon → Lyon Part Dieu : Bourgogne-Franche-Comté puis Auvergne-Rhône-Alpes).
Les cartes de réduction sont régionales : il faut alors couper le billet à la
gare frontière pour utiliser la carte de chaque région (§29 n'appliquait qu'une
région par train — la majorité de ses arrêts).

**Implémentation (pricing).**
- `PricingEngine.trip_region_segments(trip_idx, board, alight)` découpe la
  portion montée→descente d'un train le long de ses `stop_times`, en groupant
  les arrêts consécutifs de même région (`stop_region`). Chaque bascule de
  région produit un segment ; le segment suivant démarre à la gare de jonction
  (dernier arrêt de la région sortante) pour que les billets soient contigus et
  que chacun parte de la gare de coupure.
- Nettoyage : les segments de distance nulle (région limitrophe tenant sur un
  seul arrêt, ex. Île-de-France à Paris Gare de Lyon) sont absorbés dans le
  segment voisin, en remontant la gare de montée pour le premier.
- `journey_price` ajoute `pricing.split` dès qu'un leg traverse plusieurs
  régions : `junction_stations` (ex. `["Mâcon"]`), `regions`, `segments[]`
  (région, gares, km, tarif plein et réduit de la meilleure carte de la région)
  et `price_split_eur` / `price_reduced_split_eur`. Quand un train est
  découpé, `price_normal_eur` et `price_reduced_eur` affichés deviennent le
  total découpé (le billet unique n'est pas vendable en réalité) ; le billet
  unique dégressif est conservé en référence dans `split.single_ticket_eur` et
  `split.single_ticket_reduced_eur`.

**API.** Pour chaque segment découpé, `/v1/journeys` expose un `booking`
Trainline (date/heure du segment, meilleure carte de sa région ajoutée à
l'URL). Les cartes applicables incluent désormais les régions traversées d'un
train découpé (et non plus seulement sa région majoritaire).

**Web.** `priceBlock` (web/app.js) affiche un encart : « Ce train traverse
BFC et ARA : pour utiliser la carte de chaque région, découpez le billet à
Mâcon. » avec les deux billets (gares, km, prix réduit) et leur lien de
réservation, plus le total découpé.

**Exemple vérifié (K7 07:34, Paris GDL → Lyon Part Dieu, 13/08/2026).**
BFC solidaire + illico solidaire : jonction à Mâcon ; billet 1 Paris → Mâcon
(460 km, ≈ 43,90 € → 10,95 €), billet 2 Mâcon → Lyon Part Dieu (74,8 km,
≈ 17,20 € → 4,30 €) ; affiché ≈ 61,10 € → 15,25 € (billet unique de référence
≈ 47,55 € → 11,90 €).

**Tests.** `test_k7_paris_lyon_jonction_macon`,
`test_k7_paris_lyon_split_annonce_et_deux_cartes`,
`test_paris_dijon_meme_train_pas_de_split` (101 tests au total, OK).

## 33. T12 — Accords interrégionaux : découpe en 2 ou 3 billets (13/08/2026)

**Contexte.** Le §32 découpe un train multi-régions à la gare frontière en
supposant que la carte de chaque région s'applique de son côté de la coupure.
La vérification réelle montre que c'est faux : la validité des cartes
régionales sur les parcours interrégionaux dépend d'accords bilatéraux,
**directionnels** et propres à chaque paire de régions.

**Mécanisme réel (constats).**
- Le billet interrégional est vendu au barème de la région de destination aux
  gares frontières (CGV Mobigo : « pour les parcours ayant pour origine une
  gare dite frontière, seule la tarification de la région de destination est
  applicable — ex. St-Amour/Lyon ») ou au barème interrégional spécifique
  (CGV Trains ZOU! §5.3, ex. PACA/AURA, P = a + bd).
- Chaque région n'honore que ses propres cartes sur son propre barème.
- Les cartes sociales/intra-régionales (ex. ZOU! Solidaire, Solidaire+,
  Études, ZOU!, -26) ne valent que sur le réseau de leur région ; seule
  ZOU! Malin a une clause interrégionale (« 30 % sur les trajets à destination
  des régions AURA et Occitanie » — directionnelle).
- Depuis le 01/07/2022 la tarification nationale (Avantage…) n'est plus
  acceptée sur les TER/ZOU! (CGV Trains ZOU! §7.7, TER SUD).

**Cas validés (tests utilisateur).**
- **2 billets — Paris→Lyon via Mâcon (accord BFC↔AURA)** : Paris→Mâcon avec
  Mobigo (axe conventionné vers Paris Bercy/Gare de Lyon via Laroche-Migennes)
  puis Mâcon→Lyon avec illico (Mâcon = gare frontière BFC → tarif AURA).
  Fonctionne.
- **3 billets — Lyon↔Marseille (pas d'accord AURA↔PACA)** : ni illico sur
  Lyon→Bollène, ni ZOU! sur Pierrelatte→Marseille, ni ZOU! sur Marseille→Lyon.
  Le bon découpage est : billet régional AURA jusqu'à sa dernière gare
  (Pierrelatte), plein tarif interrégional Pierrelatte→Bollène-la-Croisière,
  billet régional PACA depuis sa première gare (Bollène).

**Conséquence pour le modèle (§32).** L'hypothèse « chaque région s'applique
de son côté de la coupure » n'est valide que pour les paires avec accord
bilatéral (2 billets). Pour les autres, il faut un segment interrégional plein
tarif entre les deux gares frontières (3 billets). Le §32 découpe à la
dernière gare de la région sortante (contigu) ; pour les paires sans accord il
faut découper à la frontière réelle : segment régional jusqu'à la dernière
gare de la région A, segment plein tarif jusqu'à la première gare de la
région B, segment régional ensuite. À implémenter via une table d'accords par
paire (voir §34).

## 34. T12 — Cartographie des accords interrégionaux (13/08/2026)

**Méthode.** Recherche web (13/08/2026) sur les paires de régions
métropolitaines limitrophes (pages TER régionales ter.sncf.com, CGV PDF
mmt.vsct.fr, viamobigo.fr, zou.maregionsud.fr). Verdicts de première
approche, à valider au cas par cas.

**Les deux mécanismes « 2 billets » réels.**
- (a) Coupure réelle à la frontière : deux billets contigus, la carte de
  chaque région valant jusqu'à / dès la gare de coupure — cas validé
  BFC↔AURA à Mâcon (Mobigo + illico).
- (b) Billet interrégional unique (BKRI) : la carte de chaque région donne sa
  réduction sur le trajet interrégional — la « soudure » de deux billets
  accolés sur un même train étant officiellement interdite dans toutes les
  CGV (sauf dérogations scolaires ou abonnés).

Le §32 implémente le mécanisme (a) pour tout train multi-régions. Il n'est
correct que pour les paires classées « 2 billets » ci-dessous ; pour
AURA↔PACA il faut un segment plein tarif interrégional entre les deux gares
frontières (3 billets).

**Tableau (22 paires limitrophes).**

| Paire | Verdict | Gares frontières | Notes |
|---|---|---|---|
| IDF ↔ BFC | 2 billets | Laroche-Migennes / Sens (BFC) | Mobigo valable jusqu'à Paris Bercy/GDL (axe conventionné) |
| IDF ↔ CVL | 2 billets | 1res gares IDF : Houdan, Gazeran, Dourdan | Rémi Liberté valable vers/depuis IDF |
| IDF ↔ HdF | 2 billets | Creil, Chantilly, Orry (D/H), Trie-Château (J), Plessis-Belleville (K) | Ma Carte TER HdF −50 % vers Paris |
| IDF ↔ Normandie | 2 billets | Bonnières-sur-Seine (IDF) / Vernon-Giverny (27) | Tempo Paris valable vers Paris |
| HdF ↔ Normandie | 2 billets | Formerie (HdF) / Serqueux (76) | Convention réciprocité 2025-2028 (Paris exclu) |
| HdF ↔ Grand Est | 2 billets | Hirson (02) | Réciprocité depuis 2020 (5 dép. HdF ↔ Champagne-Ardenne), hors Paris–Château-Thierry |
| Grand Est ↔ IDF | 2 billets | Dormans (51) | Fluo −50 % vers Paris Est |
| Grand Est ↔ CVL | sans objet | — | régions non limitrophes |
| BFC ↔ CVL | 2 billets | Nevers (58) | règle gare frontière : Nevers→Bourges = tarif CVL seul |
| BFC ↔ Grand Est | un seul sens | non documentées (Dijon–Troyes/Reims, Dijon/Belfort–Mulhouse) | Mobigo+ / Tarif Jeune vers GE ; carte Fluo vers BFC non documentée |
| BFC ↔ AURA | 2 billets | Mâcon, St-Amour | illico dès Mâcon (gare frontière BFC → tarif AURA) |
| CVL ↔ AURA | 2 billets | Nevers (règle gare frontière) | illico ↔ Rémi ; illico non valable Lyon→St-Pierre-des-Corps |
| CVL ↔ NAQ | 2 billets | non documentées (Vierzon/Bourges–Limoges, Châteauroux–Limoges) | Carte+ NAQ ↔ Rémi |
| NAQ ↔ AURA | 2 billets | non documentées (Ussel–Clermont, Limoges–Clermont via Montluçon) | Carte+ ↔ illico |
| CVL ↔ PDL | 2 billets | non documentées (Tours–Angers, Tours–Le Mans, Saumur–Tours) | Rémi ↔ mezzo/mobi |
| PDL ↔ NAQ | 2 billets | La Rochelle, Bressuire (gares en limite) | mezzo/mobi ↔ Carte+ ; soudure abonnés NAQ depuis 01/07/2025 |
| AURA ↔ PACA | 3 billets | Pierrelatte (AURA) / Bollène-la-Croisière (PACA) | ZOU! Malin −30 % vers AURA (titulaire seul) ; AURA→PACA sans carte |
| PACA ↔ Occitanie | 2 billets | Nîmes/Beaucaire ↔ Tarascon/Arles/Avignon | ZOU! Malin −30 % vers OCC (tit. + accompagnant) ; liO ↔ PACA |
| Occitanie ↔ AURA | 2 billets | non documentée (axe Béziers–Clermont) | liO ↔ illico ; Montpellier↔Lyon transite PACA (3 régions) |
| Occitanie ↔ NAQ | 2 billets | Valence-d'Agen (OCC) / Agen (NAQ) | barème régional Occitanie appliqué ; Carte+ −50 % vers OCC |
| PDL ↔ Bretagne | 2 billets | Redon, Vitré | offre BreizhGo « vers PDL −26 » ; tarif Nantes–Rennes |
| Normandie ↔ PDL | 2 billets | Alençon (61) | Nomad Tempo ↔ mezzo ; barème dédié depuis 01/04/2026 |
| Normandie ↔ Bretagne | sans objet | — | pas de service TER interrégional notable |

**Cas particuliers.**
- **AURA↔PACA** : seul cas « 3 billets » identifié. ZOU! Malin donne −30 %
  vers l'AURA (titulaire seul), illico n'est pas honoré côté Sud ; sens
  AURA→PACA sans aucune carte. Gares frontières : Pierrelatte (AURA) /
  Bollène-la-Croisière (PACA). Soudure scolaire tolérée (Pass ZOU! Études
  jusqu'à Bollène/Veynes + titre AURA depuis Pierrelatte/Luc-en-Diois/
  Clelles-Mens).
- **BFC↔Grand Est** : partiel (un sens documenté : Mobigo+ / Tarif Jeune vers
  GE ; carte Fluo vers BFC non documentée — CGV GE : seules Avantage Sénior
  −25 % / Liberté 0 %).
- **Paires sans service TER interrégional notable** : Grand Est↔CVL (non
  limitrophes), Normandie↔Bretagne.
- **Relations « 3 régions » hors bilatéral** : Montpellier↔Lyon (transit
  PACA), Perpignan↔Paris (Intercités).

**Conséquence modèle.** Table d'accords par paire à implémenter : défaut
« 2 billets » (mécanisme a), exception AURA↔PACA (segment plein tarif entre
les deux gares frontières), exception BFC↔GE (sens unique). À croiser avec la
vérification utilisateur des relations réellement vendues par SNCF.

## 35. T12 — Table d'accords interrégionaux : exception AURA↔PACA en 3 billets (13/08/2026)

**Implémentation.** Suite à §33/§34, la découpe contiguë « 2 billets »
(§32) devient le comportement par défaut pour les paires avec accord
bilatéral ; une nouvelle règle de segment « **gap** » (plein tarif
interrégional) matérialise les paires sans accord.

**Modèle.** `config/pricing.yaml` gagne `cross_region_rules: [[Auvergne-Rhône-Alpes, Provence-Alpes-Côte d'Azur, gap]]`, chargé et symétrisé A↔B dans `pricing.py` (`_cross_rules`). Dans `trip_region_segments()` :
- `_segment()` reçoit la région en paramètre pour dessiner explicitement le
  gap avec la région sortante ;
- boucle de découpe : transition de région → sinon gap (`_segment(i-1,i)`
  étiqueté `gap=True`) ; le gap garde la région sortante (barème
  interrégional vendu côté région de départ) mais ne peut être réduit par
  aucune carte ;
- nettoyage : fusion vers l'extrémité précédente uniquement si le dernier
  segment n'est pas un gap ; les segments gap sont toujours conservés.

**Tarification.** `journey_price()` : les segments gap sont toujours au
plein tarif (`fare_reduced_eur == fare_eur`) ; `junction_stations` n'est
rempli que si aucun gap n'est présent, sinon la frontière du gap annonce la
coupure. Le correctif §32 (prix affichés = totaux découpés) est conservé, le
billet unique restant en référence via `single_ticket_eur`. `api.py` : pas
de carte Trainline sur les segments gap (`cid=None`). `web/app.js`
(`priceBlock`) : ligne « Interrégional (plein tarif) » pour les gaps et
annonce adaptée (« pas d'accord tarifaire entre les régions, il faut un
plein tarif entre Pierrelatte et Bollène la Croisière puis la carte de la
région suivante »). `web/sw.js` : cache PWA bumpé `v4→v5`.

**Tests.** `tests/test_pricing.py` :
- `test_lyon_marseille_gap_pierrelatte_bollene` : K14 06:40 Lyon Part Dieu→
  Marseille Saint-Charles → 3 segments (Lyon→Pierrelatte AURA, gap
  Pierrelatte→Bollène la Croisière, Bollène→Marseille PACA) ;
- `test_lyon_marseille_zou_seulement_cote_paca` : ZOU! Solidaire ne réduit
  que le segment PACA, AURA + gap au plein tarif, `junction_stations=[]`.
Suite complète : 103/103 OK (dont K7 Paris→Lyon découpé à Mâcon, non
régressé).

**Vérification API (beta).** Lyon Part Dieu→Marseille Saint-Charles 06:40
avec illico SOLIDAIRE + ZOU! Solidaire :
- K14 06:40 : Lyon→Pierrelatte 27,75→6,95 (illico −75 %), gap Pierrelatte→
  Bollène 7,15 plein tarif, Bollène→Marseille 26,45→13,20 (ZOU −50 %), total
  61,35→40,55 ;
- K54 (via Valence) : même structure de gap, total 51,35→37,80.

Paris Gare de Lyon→Marseille Saint-Charles 06:40 (K7 + K4 via Avignon) :
Paris→Mâcon 43,90→10,95 (Mobigo), Mâcon→Lyon 17,20 plein, Lyon→Pierrelatte
27,75 plein, gap 7,15 plein, Bollène→Avignon 13,85→6,90 (ZOU), total
109,85→69,95. Le gap reste présent sur le dernier tronçon AURA→PACA.

**Fausse alerte.** Le 13/08 l'utilisateur a suggéré que illico « pourrait
aller de Lyon à Bollène » (donc pas de gap). Les CGV illico listent pourtant
« Sud » parmi les régions limitrophes couvertes (parcours ≤ 2 régions).
L'utilisateur s'est rétracté (« t'as raison je me suis trompé ») : le test
empirique §33 tient (illico refusé sur Lyon→Bollène), le modèle gap est
conservé tel quel.

## 36. T5 + T8 — Généralisation « toutes gares » et priorité correspondances (13/08/2026)

**Contexte.** Deux demandes du PLAN.md :
- **§5.3** : généraliser « toutes gares » à toutes les villes multi-gares (pas
  seulement Paris) ;
- **§6.2 + §8.2** : prioriser les trajets avec le moins de correspondances, même
  si cela donne un trajet plus long (ex. Paris→Marseille 2 correspondances avec
  ~4h d'attente à Lyon plutôt qu'une solution directe à 3+ correspondances).

**Analyse préliminaire.**
- **Villes multi-gares** : identification des villes avec ≥2 gares TER desservies
  par train (excluant tram, cars, homonymes). Script de vérification :
  - Paris (8 : Est, Nord, Saint-Lazare, Montparnasse Hall 1-2, Montparnasse Vaugirard, Austerlitz, Lyon, Bercy),
  - Lyon (6 : Part Dieu, Perrache, Vaise, Saint-Paul, Jean Macé, Gorge de Loup),
  - Marseille (2 : Saint-Charles, Blancarde),
  - Lille (2 : Flandres, Europe),
  - Nice (3 : Ville, Saint-Augustin, Riquier),
  - Grenoble (2 : Grenoble, Universités Gières),
  - Dijon (2 : Dijon, Porte Neuve),
  - Mulhouse (2 : Mulhouse, Dornach),
  - Metz (2 : Metz, Nord),
  - Angers (2 : Saint-Laud, Maître École),
  - Limoges (2 : Bénédictins, Montjovis),
  - La Rochelle (2 : La Rochelle, Porte Dauphine),
  - Nîmes (2 : Centre, Pont du Gard),
  - Saint-Étienne (5 : Châteaucreux, Carnot, Bellevue, Le Clapier, La Terrasse),
  - Albi (2 : Ville, Madeleine).
- **Exclusions** : homonymes hors TER (Saint-Étienne-du-Rouvray/de-Montluc/de-Cuines,
  Marseille-en-Beauvaisis, Nantes Pirmil, Rennes Beaulieu, Strasbourg Roethig, etc.)
  et gares routières/tram.
- **Priorité correspondances** : tri des résultats par nombre de correspondances
  croissant avant heure de départ (pour que les solutions à 0/1/2 transferts
  prédominent, même si plus longues).

**Implémentation.**
- **Config dynamique** : `config/place_groups.json` contient les villes et leurs
  gares. Le builder (`src/build_graph.py`) charge cette config et construit
  `Graph.place_groups` (liste d'indices) et `Graph.place_group_aliases` (alias
  de recherche → clé de groupe).
- **Résolution de lieu** : `Graph.resolve_place` :
  - « Lyon » → groupe (toutes les gares de Lyon) ;
  - « Lyon toutes gares » → groupe (via alias) ;
  - « Dijon » → gare unique (homonyme) ;
  - « Dijon toutes gares » → groupe.
- **API /v1/journeys** : paramètre `sort` prend une nouvelle valeur `transfers`
  (défaut). Le tri par défaut est donc `transfers` (nombre de correspondances
  croissant), puis `departure`, puis `duration`.
- **API /v1/stations/search** : retourne un champ `place_groups` avec les groupes
  correspondant à la recherche (ex. recherche « lyon » → groupe « Lyon »).
- **Frontend** : bouton « Moins de correspondances » ajouté, autocomplete affiche
  « Lyon — toutes les gares » comme suggestion.

**Tests.**
- Ajout de tests unitaires :
  - `test_journeys_ville_multi_gares_lyon` : « Lyon » → trajet partant d'une gare de Lyon ;
  - `test_journeys_dijon_et_dijon_toutes_gares` : différence entre « Dijon » (gare unique)
    et « Dijon toutes gares » (groupe) ;
  - `test_stations_search_place_group` : présence des groupes dans l'autocomplete ;
  - `test_sort_transfers_par_defaut` : tri par défaut par correspondances ;
  - `test_sort_transfers_correspondances_avant_depart` : comparaison tri correspondances vs départ.
- Tests existants mis à jour : `test_journeys_marche_inter_gares` (la solution à pied
  n'est plus forcément la première, mais reste présente dans les résultats).
- Tous les tests passent (110/110).

**Validation live.**
- **Lyon→Valence Ville** : trajet direct K14 depuis Lyon Part Dieu (0 correspondances).
- **Lyon→Lille** : le tri par défaut affiche d'abord un trajet à 4 correspondances
  (11:16→21:07), puis les trajets à 7 correspondances plus tôt (07:16→20:07).
  La priorité aux moins de correspondances est donc effective.
- **Autocomplete** : recherche « lyon » → suggère « Lyon — toutes les gares ».

**Déploiement.**
- **Code** : commit `f9c3d1e` sur la branche `pricing` du dépôt distant.
- **Données** : le graphe `data/graph.bin` est reconstruit avec les groupes
  dynamiques (sans redéfinir le GTFS).
- **Service** : le service `ter-finder-pricing.service` est redémarré pour charger
  le nouveau graphe et le code.
- **Frontend** : version du service worker augmentée (`ter-finder-v6`).

**Impact.**
- **Amélioration UX** : recherche simplifiée pour les grandes villes (ex. « Lyon »
  sans spécifier la gare) et trajets plus faciles à suivre (moins de changements).
- **Alignement PLAN** : les deux fonctionnalités demandées sont implémentées et testées.
- **Modification importante** : la priorité aux moins de correspondances est maintenant
  une option utilisateur (case à cocher) plutôt que le tri par défaut, pour un
  comportement moins intrusif.

## 37. T8 — Modification : priorité correspondances en case à cocher (13/08/2026)

**Contexte.** Après mise en production, l'utilisateur a suggéré que la priorité
aux moins de correspondances (implémentée en §36) était trop intrusive comme
tri par défaut. La demande est de transformer cette fonctionnalité en option
utilisateur (case à cocher) plutôt qu'un comportement systématique.

**Modification.**
- **Frontend** : 
  - Suppression du bouton "Moins de correspondances" de la barre de tri.
  - Ajout d'une case à cocher "Prioriser les moins de correspondances" sous
    la barre de tri, avec un style CSS dédié.
  - La case est décochée par défaut (tri par heure de départ).
  - Quand la case est cochée, le tri devient "transfers" (moins de
    correspondances d'abord).
- **API** :
  - Ajout du paramètre `prioritize_fewer_transfers` (booléen, défaut false).
  - Quand `prioritize_fewer_transfers=true`, le tri appliqué est "transfers".
  - Quand false (défaut), le tri reste "departure" (comportement précédent).
- **Tests** :
  - `test_sort_transfers_avec_priorisation` : vérifie que le paramètre
    `prioritize_fewer_transfers=true` trie par correspondances.
  - `test_sort_transfers_vs_departure` : compare le tri par défaut (départ)
    avec le tri activé (correspondances).

**Validation.**
- **Lyon→Lille** :
  - Sans case cochée : tri par départ (07:16, 08:16, puis 11:16).
  - Avec case cochée : tri par correspondances (4 transferts à 11:16, puis
    7 transferts à 07:16 et 08:16).
- **Frontend** : la case à cocher s'affiche correctement et change
  l'ordre des résultats quand cochée.

**Déploiement.**
- **Code** : commit `882fb1e` sur la branche `pricing`.
- **Tests** : 43 tests passent, incluant les nouveaux tests pour la case à cocher.
- **Service** : redémarré et fonctionnel.

**Impact.**
- **UX améliorée** : l'utilisateur contrôle explicitement quand privilégier
  les correspondances, plutôt qu'un changement de comportement systématique.
- **Rétrocompatibilité** : le tri par défaut reste inchangé pour les
  utilisateurs existants.

## 38. T9 — Désactivation des logs serveur et respect de la vie privée (13/08/2026)

**Contexte.** Dans le but de respecter pleinement la vie privée des utilisateurs et de pouvoir s'engager à ne stocker aucune donnée personnelle sur betater.zvz.fr, le serveur a été configuré pour supprimer toute conservation de logs d'accès.

**Modifications.**
- **Nginx** :
  - Modification de `/etc/nginx/sites-enabled/betater.zvz.fr` pour remplacer `access_log ...` par `access_log off;`. Les logs d'accès ne sont plus écrits sur le disque.
- **API (Uvicorn / FastAPI)** :
  - Modification de `/etc/systemd/system/ter-finder-pricing.service` pour ajouter l'option `--no-access-log` à Uvicorn. Les requêtes HTTP ne sont plus enregistrées dans le journal système.
- **Politique de confidentialité** :
  - Mise à jour de [privacy.html](file:///home/ubuntu/ter-finder-pricing/web/privacy.html) (section *Côté serveur*) pour refléter cette absence totale de logs de connexion, expliquer le rôle éphémère du routage d'Oracle Cloud / FAI, et préciser de façon transparente que sous réquisition légale ou décision de justice, les IPs peuvent être loguées au niveau du réseau intermédiaire mais sans aucune visibilité sur le contenu des recherches grâce au chiffrement HTTPS.

**Validation.**
- Rechargement de systemd, redémarrage de `ter-finder-pricing.service` et rechargement de Nginx.
- Un test HTTP (curl) confirme qu'aucune requête n'est plus loguée par Nginx dans `betater_zvz_fr_access.log`, ni par Uvicorn dans journalctl.

## 39. T13 — Création de la page À propos (13/08/2026)

**Contexte.** Ajout d'une page manifestant la philosophie du projet PlanTER (l'école buissonnière du rail, le voyage vs la vitesse, la liberté tarifaire des cartes de réduction, le paiement en espèces anonyme en gare de campagne et l'importance de ralentir).

**Modifications.**
- **Page à propos** : Création de `web/about.html` contenant le texte philosophique rédigé en français.
- **Footer** : Modification de `web/index.html` pour insérer un lien vers `/about.html` dans le footer du site à côté des cartes de réduction et de la confidentialité.

**Validation.**
- Page accessible localement et sur le serveur de démonstration. Le style visuel s'intègre harmonieusement avec la charte graphique existante.

## 40. T8bis — Déplacement de la case Prioriser les moins de correspondances (13/08/2026)

**Contexte.** La case à cocher "Prioriser les moins de correspondances" influait directement sur les paramètres de recherche de l'API. Cependant, elle n'était affichée que dans les résultats de recherche (qui sont masqués par défaut). L'option a été déplacée dans le formulaire de recherche principal pour permettre de la sélectionner dès le départ.

**Modifications.**
- **HTML** : Déplacement de la case à cocher `#prioritize-fewer-transfers` depuis la section `#results` vers le formulaire `#search-form` (avec un affichage pleine largeur et un libellé plus explicite).
- **JavaScript** : Ajustement de l'écouteur `change` dans `web/app.js` pour ne déclencher automatiquement la recherche que si les résultats sont déjà affichés sur la page.
- **PWA Cache** : Incrémentation de la version du cache (`ter-finder-v7`) dans `web/sw.js` et ajout de `/about.html` aux ressources du shell.

**Validation.**
- L'interface affiche la case dès le premier chargement.
- La sélection de la case modifie dynamiquement le tri si des résultats sont visibles, ou est simplement conservée pour la première recherche dans le cas contraire.
- Les tests unitaires et d'intégration passent tous avec succès (43/43 tests OK).


## 41. T12 — Correction du Découpage Malin pour les trajets multi-legs (16/08/2026)

**Contexte.** Lors de trajets multi-legs (ex. Paris → Grenoble avec correspondance à Lyon), l'astuce de découpage régional "Découpage Malin" n'affichait que les billets du train découpé (ex. Billet 1 : Paris → Mâcon, Billet 2 : Mâcon → Lyon) et omettait le leg non découpé (ex. Lyon → Grenoble). De plus, le prix total affiché pour l'option de découpage n'incluait que le prix du train découpé.

**Modifications.**
- **Moteur de tarification (`src/pricing.py`)** :
  - Ajout des métadonnées de leg (`from_id`, `to_id`, `departure_min`, `arrival_min`) à chaque entrée du tableau `legs` retourné par `journey_price`.
  - Lors de la construction de la structure `split`, les legs non découpés sont maintenant insérés comme des segments unitaires uniques dans `split["segments"]`. Cela garantit que la totalité des billets de bout en bout nécessaires au voyage est présente dans la liste des billets du découpage.
- **Frontend (`web/app.js`)** :
  - Remplacement des mentions statiques de billets ("2 Billets Découpés", "Découpage Malin (2 billets)") par des valeurs dynamiques utilisant la longueur réelle de la liste des segments (`sp.segments.length`).
  - La description de la coupure affiche "Tarifs cumulés" au lieu d'une gare de coupure si aucune coupure de train n'a eu lieu sur un segment donné.
- **Tests (`tests/test_pricing.py`)** :
  - Ajout d'un test d'intégration `test_multi_leg_journey_with_split` qui combine un train découpé (Paris → Lyon via Mâcon) et un train direct (Lyon → Grenoble) et valide que le split retourné contient bien les 3 segments attendus avec les bonnes gares d'origine et de destination.

**Validation.**
- Exécution de la suite de tests unitaires de pricing (`.venv/bin/python -m unittest tests.test_pricing -v`), validant le comportement et confirmant la réussite de tous les cas.

## 42. T5 — Stabilisation du test d'arrivée jour+1 face aux variations GTFS (16/08/2026)

**Contexte.** Le test `test_journeys_arrivee_jour_suivant` vérifiait que l'API formate correctement les arrivées après minuit sous la forme `jour+1` (ex: `2026-08-11T00:31:00`). Toutefois, l'évolution quotidienne du GTFS national de la SNCF a introduit de nouveaux trajets directs en journée (1 correspondance au lieu de 2), dominant ainsi le trajet de nuit qui servait au test.

**Modifications.**
- **Tests (`tests/test_api.py`)** :
  - Utilisation de `unittest.mock.patch` pour simuler de manière déterministe un trajet de nuit avec correspondance arrivant le lendemain (2 correspondances, arrivée à 00:31 J+1).
  - Cela élimine le flou lié aux mises à jour quotidiennes de la base GTFS SNCF et pérennise la validation de la datation `J+1` sans perturber le reste de la suite de tests.

**Validation.**
- Tous les tests passent avec succès (`.venv/bin/python -m unittest discover tests`), affichant `OK` pour les 122 cas testés.

## 44. T9/T11 — Suppression complète des liens externes Trainline (16/08/2026)

**Contexte.** Suite aux instabilités et redirections imprévisibles de l'application tierce Trainline, décision de supprimer intégralement les liens et boutons de réservation externes de l'interface et de l'API.

**Modifications.**
- **Frontend (`web/app.js`, `web/privacy.html`, `web/sw.js`)** :
  - Suppression de la colonne « Achat » dans le tableau synthétique (Style C), remplacée par un affichage textuel clair et détaillé des billets composant le Découpage Malin.
  - Suppression des boutons d'achat/réservation externes dans les cartes tarifaires (Style A) et le bandeau synthétique (Style B).
  - Suppression de la section « Liens externes » dans `privacy.html`.
  - Incrémentation du cache Service Worker (`ter-finder-v10` / `ter-finder-api-v4`).
- **Backend (`src/api.py`, `tests/test_api.py`)** :
  - Nettoyage du endpoint `/v1/journeys` pour ne plus générer d'objets `booking` superflus.
  - Nettoyage des tests unitaires obsolètes.

**Validation.**
- 118 tests unitaires exécutés et validés (`OK`).
- Redémarrage du service `ter-finder-pricing.service`.

## 45. T12 — Intégration du tarif direct de référence Paris-Lyon (65,00 €) et calcul du gain réel (16/08/2026)

**Contexte.** Le tarif unitaire standard d'un billet direct Paris–Lyon sur TER (sans astuce de découpage) est de 65,00 € au barème national SNCF. Sans cette dérogation, le moteur calculait un faux prix direct théorique mono-région BFC (41,00 € + 26,90 € = 67,90 €), ce qui faisait apparaître le Découpage Malin à 72,60 € plus cher que le prétendu billet direct, alors qu'il est en réalité plus économique que le tarif direct réel (65,00 € + 15,80 € = 80,80 €).

**Modifications.**
- **Configuration (`config/pricing.yaml`)** :
  - Ajout des dérogations d'axe `["Paris", "Lyon", 65.00]` et `["Lyon", "Paris", 65.00]`.
- **Moteur de tarification (`src/pricing.py`)** :
  - Calcul du tarif direct de référence avec prise en compte des dérogations d'axe pour tous les tronçons d'un voyage.
  - Pour Paris–Lyon–Grenoble : Billet Direct de référence = 65,00 € + 15,80 € = **80,80 €** ; Découpage Malin (3 billets) = 41,00 € + 15,80 € + 15,80 € = **72,60 €** (Économie : **−8,20 €**).
- **Frontend (`web/app.js`, `web/sw.js`)** :
  - Utilisation de `directPrice` (prix unitaire direct de référence sans astuce) pour l'affichage de la carte et de la ligne "Billet Direct".
  - Incrémentation du cache Service Worker (`ter-finder-v11` / `ter-finder-api-v5`).
- **Validation** :
  - 118 tests unitaires au vert (`OK`).
  - Redémarrage du service `ter-finder-pricing.service`.


