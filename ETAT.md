# État du projet ter-finder-pricing (big-arm) — 15 août 2026

## Ce qui fonctionne ✅

| Composant | État | Détail |
|-----------|------|--------|
| `src/rfn.py` | **Modifié** | Jonctions RFN alignées sur le PK de la gare (St-Germain-des-Fossés, Vichy, etc.). Suppression des raccourcis lignes de service (886000/888000/782000). |
| Init `RfnIndex` | **Optimisé** | 77 s → 4,8 s grâce à la grille spatiale (cellule 0,03°). |
| Connexions | 3782 → 2520 | Seules les vraies jonctions aux gares restent. |
| Chaîne K2 (GTFS) | **Testée** | 228,565 km → 44,00 € (écart 0 € vs prix officiel). |
| `Graph.hop_km` | **Précalculé** | Distances PK par hop (arrêts consécutifs → UIC → `RfnIndex.hop_km`) : **272 816/312 892 hops couverts (87,2 %)**, fallback haversine × rail_factor pour le reste. |
| `src/pricing.py` | **Câblé** | `leg_km()` / `_segment()` somment `Graph.hop_km` (PK) avec repli haversine. |
| Rebuild graph | ~83 s | 78 s PK precompute + 5 s build ; load 0,54 s (✓ < 2 s). |
| Revalidation AURA | **8/8** | Tous les écarts à 0,00 € (dont Clermont-Lyon 44,00 €). |
| Site betater.zvz.fr | **44,00 €** | API `uvicorn:8001` redémarrée (nouveau graph.bin), K2 Clermont → Lyon Perrache = 44,0 €. |
| Segments interrégionaux (gap) | **Matrice moyenne** | Prix d'un segment « plein tarif » AURA↔PACA = moyenne des barèmes des 2 régions : Pierrelatte → Bollène 11,6 km = 4,50 € (vs 4,30 réel, au lieu de 3,80). Valence → Marseille total = 54,60 € (vs 54,90 réel). |
| Note « calibré sur 3 prix » | **Supprimée** | `note` retiré de pricing.py et de l'affichage web. |
| Bug multi-leg + split | **Corrigé** | Le total découpé ignorait les legs simples (Lyon → Nice = 75 € au lieu de 127,9 €). Désormais les legs sans découpage entrent dans `price_split_eur`. |
| Prix par tronçon dans l'UI | **Ajouté** | Chaque leg affiche désormais gares + `fare_eur`/`fare_reduced_eur`, même sans changement de région (avant : uniquement dans le bloc split). |
| BFC plafond 41 € | **Corrigé** | Mobigo = grille par paliers « 6 à 41 € maxi » (ter.sncf.com). Le repli > 159 km montait sans borne (Paris→Mâcon 439,7 km → 42,85 €). Ajout de `max_eur` (plafond) + plancher au dernier palier → **41,00 €**, monotone. |
| BFC grille officielle | **Appliquée** | CGV TRAIN Mobigo « Tarif de base régional » (01/04/2026) : paliers km → 1-35:6 €, 36-70:13 €, 71-100:19 €, 101-135:24 €, 136-165:31 €, 166-200:36 €, 201+:41 € (enfant 2 €). Remplace la grille partielle 6/13/19/31 + repli. Sources : `Tarifs_de_base_TRAIN_Mobigo_au_01_04_2026.pdf` (mmt.vsct.fr), dépliant TRAIN Mobigo juil. 2025 (6/13/18/23/30/35/39, réindexé +1 en 2026), CGUV 04/2025. 6 points observés Trainline revalidés à 0,00 € d'écart. |
| Grand Est règle | **Ajoutée** | Barème officiel CGV TER GE V38 (08/2026), tarifs 01/01/2026 p.23 : affine 11 paliers, palier fixe 1-10 km = 3,20 €, min perception 1,20 €. Barème extrait des images de la CGV (pdftotext les ignorait) + lu par l'utilisateur. Validation 15/08 : Reims-Charleville 0,00 €, Strasbourg-Colmar +0,20, Strasbourg-Mulhouse +0,40, Strasbourg-Nancy +0,80 (km modèle 149,6 vs réel ~151), Metz-Nancy +2,80 (km modèle 70,9 vs réel ~57 — écart distance, pas barème). Strasbourg-Nancy : 31,80 € vs 24,65 € avant. |
| Pays de la Loire règle | **Ajoutée** | Barème officiel CGV TER PDL 25/06/2026 p.22, tarifs 01/07/2026 : affine 10 paliers. = PRIX DE RÉFÉRENCE plein tarif. Le produit vendu est le billet ecco (promo dynamique 1-22 €, J-8/J-1) qui ne suit pas le barème → pour les prix réels ecco, se référer au cache/Tictactrip (note dans pricing_rules.yaml). |
| Occitanie règle | **Ajoutée** | Barème affine BKN lu par l'utilisateur (15/08) — confirmé par l'utilisateur comme issu des **CGV** (résultat de la CGV liO). Remplace `external` (cache/Tictactrip). min 1,10 €, arrondi au décime supérieur. Validation : Toulouse-Montauban −0,30, Toulouse-Albi −0,70, Toulouse-Nîmes +0,90 (moy 0,63 €, pire 0,90 € < 1 €). API Toulouse→Albi = 15,50 € (73,0 km). Prix promos Futé écartés des références (Toulouse-Carcassonne 10 €, Nîmes-Montpellier 3 €). |
| Normandie règle | **Ajoutée (grille CGV)** | Grille **CGV Nomad 30/03/2026** (plein tarif TEMPO 2nde) fournie par l'utilisateur : 4,40 €/25 km jusqu'à 200 km puis +2,20 €/20 km jusqu'à 400 km (plafond 57,20 €, 1ère classe ×1,5). Remplace l'`escalier_step` 4,40/25 avec repli scale 0,9575 au-delà de 110 km. Résout l'« outlier » Caen-Cherbourg : km modèle 131,4 → bande 126-150 = 26,40 € (exact). Validation 4/4 à 0,00 €. API Caen→Cherbourg = 26,40 €. Interrégions : CVL = a+bd, HdF/PDL = barème réciproque spécifique. |
| Bug distance PK jonctions | **Corrigé** | `src/rfn.py::_build_connections` ancrait les jonctions-gare au même PK des deux côtés (`(lignea, pkb, code, pkb)`), alors que les PK sont spécifiques à chaque ligne. Ex. Angers : 515000 à 342,95 mais 450000 à 306,26 → Sablé→Angers faux (84 vs 47 km), Nantes→Le Mans 219,4 km. Corrigé : `(lignea, pkb, code, pka)` = PK propre de chaque ligne. Rebuild graph (26 s) : Nantes→Le Mans = 182,7 km (SNCF : 183) → 37,40 € vs réel plein 37,70 € → écart résiduel 0,30 € **accepté** (distance modèle ok ; le 37,70 réel correspond à ~185 km tarifaires). Autres régions inchangées (validate_rules identique). |
| Feed TRSI Transdev | **Fusionné** | Le GTFS national SNCF ne couvre pas les trains ZOU! Transdev RSI (Marseille ↔ Nice directs, ex. 17481). Ajout `data/ter/gtfs_trsi.zip` (transport.data.gouv.fr, dataset « ZOU ! Transdev Rail Sud Intermétropole », ressource 83448) + option `--extra-input` dans `src/build_graph.py` : 3881 trips datés, 1 ligne SUD_IV15, 19 gares alignées sur les StopArea existantes (UIC nus → `StopArea:OCE…`). API Marseille→Nice : directs 0 corr. à 41,50 €, vehicle_label = numéro de train (regex TRSI `^(\d+)@` ajoutée dans `raptor.py`/`gtfs_rt.py`/`api.py`). Graph : 41098 trips. + `test_direct_marseille_nice_trsi`. |
| Tests | **76/76** | `test_fare_ancre_hdf` corrigé (22,20 → 23,10 € : scale HdF 1.0425). Point PDL Nantes-Le Mans au km tarifaire réel (185,0) → 0,00 €. + `test_distance_pk_jonction_angers` (régression : Nantes-Le Mans ~183 km). + `test_fare_affine_occitanie` (3 points) ; `test_fare_plancher_et_arrondi` et `test_fare_region_inconnue` basculés sur « INCONNUE » (Occitanie a désormais sa règle). `test_fare_ancre_normandie` resynchronisé sur la grille CGV Nomad (112 km → 22,00 €, + bornes de paliers 25/26/50/51 et 131,4 km → 26,40 €). |

## Fichiers modifiés (15/08)

| Fichier | Action |
|---------|--------|
| `src/graph.py` | Champ `Graph.hop_km` : `(trip_idx, k) → km PK` entre arrêts consécutifs. |
| `src/build_graph.py` | Précalcul PK par hop via `RfnIndex` (une seule passe sur les trips). |
| `src/pricing.py` | `_hop_km()` (PK si dispo, sinon haversine × rail_factor) ; `leg_km()`/`_segment()` l'utilisent. Gap interrégional = `_cross_fare()` (matrice moyenne des 2 régions). Note « calibré sur 3 prix » supprimée. |
| `src/rfn.py` | Index `_conn_by_line` construit une fois (au lieu de reconstruire `by_line` à chaque dijkstra) : 141,7 s → 77,8 s de precompute. **+ bug jonctions-gare corrigé** : connexions au PK propre de chaque ligne (`pka` côté ligne B au lieu de `pkb` partout). |
| `config/pricing.yaml` | Règle BFC : grille complète Mobigo CGV 01/04/2026 (7 paliers, plafond 41 €) au lieu de 6/13/19/31 + repli 0,9865. Règles Grand Est + Pays de la Loire + Occitanie (affine, barèmes CGV officiels 2026). **Règle Normandie : grille CGV Nomad 30/03/2026 complète (18 paliers → 57,20 €), scale 1.0, fin du repli 0,9575.** |
| `data/pricing_rules.yaml` | GE : formula → affine officiel CGV V38 ; PDL : external → affine officiel CGV 25/06/2026 (note ecco promo) ; Occitanie : external → affine BKN (note Futé promo conservée). **Normandie : escalier_step → escalier grille CGV Nomad (18 paliers) + note interrégions CVL/HdF/PDL.** |
| `validate_rules.py` | +5 points Grand Est, +1 point Pays de la Loire, +3 points Occitanie. **Points Normandie resynchronisés sur km modèle PK : Rouen-Dieppe 59,5 ; Caen-Cherbourg 131,4 → 26,40 € (plus d'OUTLIER).** |
| `tests/test_pricing.py` | +`test_fare_affine_grand_est`, +`test_fare_affine_pays_de_la_loire`, +`test_fare_affine_occitanie` ; `test_fare_ancre_hdf` resynchronisé sur scale HdF 1.0425 (23,10 €) ; `test_fare_plancher_et_arrondi` et `test_fare_region_inconnue` basculés sur région « INCONNUE ». |
| `ETAT.md` | Lignes grille BFC officielle, Grand Est, Pays de la Loire. |

## Déploiement

Contrairement à ce qu'écrivait la version précédente de ce fichier, `get_engine()` (src/api.py) ne recharge le graphe qu'une fois par processus : après rebuild de `graph.bin`, **il faut redémarrer uvicorn** (fait le 15/08, PID courant sur :8001).

## Notes

- `src/rfn.py` n'est **pas commit** (visible en `git status` comme `?? src/rfn.py`).
- `data/graph.bin` date du 15/08 07:44 (avec distances PK).
- HdF : `config/pricing.yaml` (scale 1.0425) et `data/pricing_rules.yaml` (scale 1.0) divergent encore — le moteur arrondit à 23,10 € sur l'ancre 112 km. À resynchroniser quand la calibration HdF sera figée.