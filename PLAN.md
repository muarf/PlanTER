# PLAN — TER Finder : recherche de trajets 100% TER en France

> Ce document est le cahier des charges du projet **TER Finder** : un moteur de recherche d'itinéraires qui ne propose QUE des trajets en train régional (TER), à destination d'un site web et d'une application mobile.
>
> Il est rédigé pour être **divisé en tâches indépendantes**, chacune confiée à un agent IA. Chaque section « Tâche » est auto-contenue : objectif, spécifications, critères d'acceptation, dépendances.
>
> **Décisions validées (ne pas remettre en cause sans discussion) :**
> - Périmètre : **trains TER + cars TER** (pas de TGV, Intercités, OUIGO, ICE, Lyria, ni cars à réservation).
> - Stack : **Python / FastAPI** (moteur + API).
> - UI : **site web d'abord**, app mobile ensuite.
> - **Gratuit pour les utilisateurs.** Monétisation éventuelle par affiliation billetterie, étudiée plus tard. Aucun paiement dans ce projet pour l'instant.
> - Ordre de build obligatoire : T1 → T2 → T3 → T4 → T5 → T6 (+ T7 à T9 ensuite).

---

## Table des matières

1. [Contexte et objectif](#1-contexte-et-objectif)
2. [Cas d'usage de référence](#2-cas-dusage-de-référence)
3. [Sources de données](#3-sources-de-données)
4. [Filtrage des TER (méthode validée)](#4-filtrage-des-ter-méthode-validée)
5. [Modèle de données et graphe](#5-modèle-de-données-et-graphe)
6. [Algorithme de calcul d'itinéraires](#6-algorithme-de-calcul-ditinéraires)
7. [API REST](#7-api-rest)
8. [Interface web](#8-interface-web)
9. [Application mobile](#9-application-mobile)
10. [Temps réel (v2)](#10-temps-réel-v2)
11. [Monétisation](#11-monétisation)
12. [Licence et obligations](#12-licence-et-obligations)
13. [Risques et points d'attention](#13-risques-et-points-dattention)
14. [Découpage en tâches pour les agents IA](#14-découpage-en-tâches-pour-les-agents-ia)
15. [Roadmap](#15-roadmap)

---

## 1. Contexte et objectif

Les sites et applications existants (SNCF Connect, Google Maps, Trainline…) calculent des itinéraires multi-modes et privilégient systématiquement le TGV/Intercités dès qu'une liaison longue distance existe. Résultat : de nombreux trajets réalisables **uniquement en TER** (avec correspondances régionales) sont invisibles ou très difficiles à trouver, même s'ils existent.

**Objectif de TER Finder :** proposer un outil de recherche d'itinéraires qui ne montre **que des trajets composés exclusivement de trains TER (et cars TER)** , avec jusqu'à **3 correspondances**, et qui les mette en avant avec des horaires fiables issus des données officielles SNCF.

Positionnement :
- **Gratuit** pour les utilisateurs.
- Valeur : trouver des trajets régionaux « cachés » que les gros outils ignorent.
- Cible v1 : site web ; v2 : application mobile ; v3 : éventuelle monétisation par affiliation.

## 2. Cas d'usage de référence

Trajet **Paris → Dijon → Besançon** (trajet réel de l'utilisateur, départ depuis
**Paris Gare de Lyon**, parfois **Paris Bercy**) :

- Le « direct » vers Besançon proposé partout est la route `611A | Paris - Besançon Viotte` : c'est un **TGV INOUI** (vérifié dans les données). → À exclure.
- Le trajet 100% TER existe (vérifié dans les données du 10/08/2026) :
  - **Paris Gare de Lyon → Dijon** : route `K7 | Paris - Lyon P D` (TER). Direct certains jours (ex. 07:34 → Dijon 10:33 → Lyon Part Dieu 12:44), sinon 1 changement via Laroche-Migennes.
  - **Paris Bercy → Dijon** : direct `K7` certains jours, sinon 1 changement (route `P25 | Paris Bercy - Avallon` → Laroche-Migennes, puis `K7` → Dijon).
  - **Dijon → Besançon** : route `C11 | Dijon-Besançon` (TER), direct.
- **Cas canonique** : `Paris Gare de Lyon → Besançon Viotte` en **1 seul changement à Dijon** (K7 puis C11), arrivée 12:04 le 10/08/2026.

> ⚠️ Les lignes « directes » ne circulent **pas tous les jours** (cf. §4.5) : le cas
> canonique doit toujours être résolu à la date demandée via `calendar_dates`, jamais
> supposé régulier.

Ce trajet doit être **retrouvé et affiché** par TER Finder, en tête des résultats.
Il sert de test de validation de bout en bout (cf. Tâches T2, T3, T4).

## 3. Sources de données

### 3.1 Jeu de données principal (données statiques)

| Élément | Valeur |
|---|---|
| Nom | Réseau SNCF TGV, Intercités et TER — HORAIRES SNCF |
| Fichier | `Export_OpenData_SNCF_GTFS_NewTripId.zip` |
| URL | `https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip` |
| Taille | ≈ 4,6 Mo |
| Cadence | régénéré quotidiennement |
| Validité | ≈ 133 jours à venir (mesuré le 10/08/2026) |
| Format | GTFS (Standard + extensions) |
| Licence | **ODbL** + Conditions Particulières d'utilisation (voir §12) |

Contenu mesuré sur l'export du 10/08/2026 (servir de référence, les chiffres varient légèrement) :
- 696 lignes (`routes.txt`)
- 47 296 courses (`trips.txt`), horaires via `calendar_dates.txt` (236 639 lignes)
- 409 812 `stop_times.txt`
- 8 906 entrées `stops.txt` (StopArea + StopPoint)
- `transfers.txt` : **vide** (uniquement l'en-tête) → les correspondances sont à construire nous-mêmes (§5.3)
- Pas de `shapes.txt`, pas de données tarifaires.
- **Validité du GTFS : 133 jours (2026-08-09 → 2026-12-19)** — toute requête hors plage doit être rejetée (erreur 400).

> ⚠️ Historique : l'ancien jeu « TER seul » (`export-ter-gtfs-last.zip`) a été fusionné dans ce jeu global (été 2025). Il n'existe plus de fichier TER pur : **le filtrage TER est notre responsabilité** (voir §4).

### 3.2 Données temps réel (phase 2 uniquement)

- **GTFS-RT Trip Updates** (retards, suppressions) : `https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates` (mis à jour toutes les 2 minutes, horizon 60 min).
- **GTFS-RT Service Alerts** (perturbations) : `https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-service-alerts`.

Utilisées uniquement dans la Tâche T8 (v2). En v1, **horaires théoriques uniquement**.

## 4. Filtrage des TER (méthode validée)

### 4.1 Principe : le mode commercial est encodé dans les `stop_id`

Dans `stop_times.txt`, chaque arrêt référence un `stop_id` du type :

```
StopPoint:OCE<MODE COMMERCIAL>-<code UIC de la gare>
```

Exemples observés dans le fichier réel :
```
StopPoint:OCETrain TER-87713040      → train TER à Dijon
StopPoint:OCECar TER-87713040        → car TER à Dijon
StopPoint:OCETGV INOUI-87713040      → TGV INOUI à Dijon
StopPoint:OCEINTERCITES-87713040     → Intercités à Dijon
```

> Implémentation (`gtfs.extract_mode`) : le jeton extrait entre `StopPoint:OCE` et
> le premier `-` est **SANS le préfixe `OCE`** (`Train TER`, `TGV INOUI`, …). La
> whitelist/blacklist de `config.py` doit donc comparer ces jetons nus.

### 4.2 Règles de filtrage (whitelist / blacklist)

| Préfixe StopPoint | Mode | Inclus ? |
|---|---|---|
| `OCETrain TER` | Train TER | ✅ OUI |
| `OCECar TER` | Car TER (offre régionale) | ✅ OUI |
| `OCETramTrain` | Tram-train régional | ✅ OUI |
| `OCETrain` | Train générique (cas rare) | ✅ OUI |
| `OCETGV INOUI` | TGV Inoui | ❌ NON |
| `OCEOUIGO` | OUIGO | ❌ NON |
| `OCEINTERCITES` | Intercités | ❌ NON |
| `OCEINTERCITES de nuit` | Intercités de nuit | ❌ NON |
| `OCEICE` | ICE | ❌ NON |
| `OCELyria` | Lyria | ❌ NON |
| `OCECar à réservation` | Autocar longue distance | ❌ NON |

### 4.3 Méthode de filtrage (obligatoire)

1. Lire `stop_times.txt` ligne par ligne.
2. Pour chaque ligne, extraire le mode depuis le préfixe du `stop_id` (tout ce qui est entre `StopPoint:OCE` et le premier `-`).
3. Construire la table `trip_id → mode commercial`.
   - Vérification faite sur les données réelles : **tous les arrêts d'une même course partagent le même mode**. La première occurrence suffit, mais on peut vérifier la cohérence sur toutes les lignes d'un même trip.
4. Garder **uniquement** les `trip_id` dont le mode est dans la whitelist (les 4 premiers lignes du tableau).
5. Propager le filtre aux autres fichiers :
   - `trips.txt` : ne garder que les trips conservés.
   - `routes.txt` : ne garder que les routes ayant au moins un trip conservé.
   - `stop_times.txt` : ne garder que les lignes des trips conservés.
   - `calendar_dates.txt` : ne garder que les `service_id` encore utilisés.
   - `stops.txt` : ne garder que les arrêts encore référencés.
6. Ré-écrire un **GTFS-TER « propre »** (fichier ou objet en mémoire) en sortie.

> Note : une même route peut avoir des trips TER train ET TER car (ex. `Dijon - Besançon`) : c'est normal, les deux restent dans l'offre TER. En revanche aucune route ne mélange TER et non-TER (vérifié) — le filtrage au niveau `trip` est donc sans effet de bord.

> ⚠️ **Le `route_short_name` n'identifie PAS une ligne physique.** « K7 » recouvre
> plusieurs routes distinctes (ex. `FR:Line::B10C45A0-…` = Paris Gare de Lyon/Bercy –
> Dijon – Lyon, mais `FR:Line::7B48FDEF-…` = Marseille – Avignon). Toute identité de
> ligne se fait via le **`route_id`** ; le `short_name` n'est qu'une étiquette
> d'affichage (des doublons peuvent s'afficher, c'est normal).

### 4.4 Étiquetage du type de véhicule

Le mode commercial doit être **préservé** dans le graphe (champ `vehicle_type` par trip : `train`, `car`, `tram_train`) pour que l'UI affiche clairement « Train TER 839565 » vs « Car TER 841235 » et propose un filtre « Trains uniquement ».

## 5. Modèle de données et graphe

### 5.1 Entités

- **StopArea** (`location_type=1`) : la gare (ex. Dijon). Identifiée par `StopArea:OCE87713040`.
- **StopPoint** (`location_type=0`) : un arrêt physique de la gare, lié à une StopArea via `parent_station`. Ex. `StopPoint:OCETrain TER-87713040`.
- **Route** : ligne (ex. route_id `FR:Line::…:`, short_name `K5`, long_name `PARIS - DIJON`).
- **Trip** : une course de la ligne (un train concret à une date/heure). Porte le `vehicle_type` (§4.4).
- **StopTime** : passage d'un trip à un arrêt (heure arrivée + départ).
- **Service** : un `service_id` + ses dates de validité (`calendar_dates.txt` : les exceptions positives (type 1 = service ajouté) et négatives (type 2 = service supprimé)).

### 5.2 Gestion des horaires

- Convertir tous les horaires GTFS (format `HH:MM:SS`, pouvant dépasser 24h) en **minutes depuis minuit du jour de départ** (ex. `26:15` → `26*60+15 = 1575`). Les horaires > 1440 min indiquent un service passant minuit : gérer le chevauchement sur le jour suivant.
- Déterminer si un trip circule un jour J : lire son `service_id`, appliquer les exceptions `calendar_dates` (1 = ajouté, 2 = retiré).

### 5.3 Correspondances (transferts)

`transfers.txt` est vide. Construire notre propre règle de correspondance :

- **Règle par défaut** : tous les StopPoints appartenant à la **même StopArea** sont connectés avec un temps de marche fixe.
  - Valeur par défaut : **5 minutes** (minimum de correspondance).
  - Grandes gares (Dijon, Paris Gare de Lyon, etc.) : 8–10 minutes, calibrées dans un fichier de configuration `interchange.yaml` (liste de `stop_area_id → minutes`), pré-remplie avec les gares principales.
- **Cas multi-gares (Paris)** : les 7 gares parisiennes (Est, Nord, Lyon, Bercy, Montparnasse, Saint-Lazare, Austerlitz) sont des StopAreas distinctes. Deux niveaux de gestion :
  - **v1 (simple)** : chaque gare est un point d'origine/destination distinct. L'utilisateur choisit « Paris Gare de Lyon », « Paris Bercy », etc. On ajoute un petit **graphe de marche/métro intra-Paris** dans un fichier de config (ex. Paris Est ↔ Paris Nord = 8 min à pied ; Paris Gare de Lyon ↔ Paris Bercy = 10 min ; Paris Est ↔ Paris Gare de Lyon = 20 min en métro ligne 5 ; etc.) pour permettre les correspondances inter-gares si elles sont utiles. Ces arcs de marche sont **optionnels** et désactivables.
  - **v2 (évolué)** : regrouper « Paris » comme un nœud unique multi-gares avec autocomplete intelligent (voir Tâche T6/T7).
- **« Toutes gares » (§5.5) implémenté dès T2** : la recherche accepte « Paris » ou
  « Paris toutes gares » comme un **groupe** de 7 gares (Est, Nord, Saint-Lazare,
  Montparnasse Hall 1 - 2, Austerlitz, Gare de Lyon Hall 1 - 2, Bercy) — atteindre
  l'une d'elles satisfait la recherche. Codé par `Graph.place_groups["paris"]` +
  `Graph.resolve_place()` (graphe) et `_find_areas()` (vérificateur) ; l'arrivée à
  n'importe quel membre compte, le routage inter-gares utilisant `paris_links`.

### 5.4 Index de recherche des gares

- Table `stop_area → { nom, coordonnées, alias }`.
- Normalisation : accents ignorés (« Besançon » ≡ « Besancon »), alias courants (ex. « Bordeaux St-Jean », « Paris Gare de Lyon », « Marseille St-Charles », codes UIC).
- Recherche par préfixe + score de similarité pour l'autocomplete.

## 6. Algorithme de calcul d'itinéraires

### 6.1 Choix : McRAPTOR (multi-critères Pareto)

Le réseau étant **dépendant du temps** (les horaires priment sur les distances), les algorithmes de plus court chemin classiques (Dijkstra/A*) sur graphe « temps-expandu » sont sous-optimaux pour notre besoin multi-critères (minimiser l'heure d'arrivée ET le nombre de correspondances simultanément).

**McRAPTOR** (Round-bAsed Public Transit Optimized Routing, Delling/Pajor/Werneck) est le standard du domaine :
- Parcourt le réseau **par rounds** : round 0 = trajet direct, round 1 = 1 correspondance, round 2 = 2 correspondances, etc.
- **La limite « jusqu'à 3 correspondances » = simplement s'arrêter après le round 3.**
- Produit **l'ensemble des trajets Pareto-optimaux** : on ne garde un trajet que s'il n'en existe pas un autre strictement meilleur (arrive plus tôt avec au plus autant de correspondances, etc.).
- **Aucun préprocessing lourd** → simple à implémenter, robuste aux mises à jour d'horaires.
- **Performance** : sur ce réseau (~700 lignes, ~48 000 trips), les requêtes répondent en quelques millisecondes.

Références :
- Delling, Pajor, Werneck, « Round-Based Public Transit Routing », ALENEX 2012 (papier original RAPTOR).
- McRAPTOR (multi-critères) : extension décrite dans le même papier.

### 6.2 Critères d'optimisation

Obligatoires :
1. **Heure d'arrivée la plus précoce** (pour une recherche « départ au plus tôt »).
2. **Nombre de correspondances minimal** (0 à 3).
3. **Heure de départ la plus tardive** (pour une recherche « arrivée au plus tard »).

Optionnels (v2) :
4. Durée totale minimale.
5. Préférence « trains uniquement » vs « trains + cars ».

### 6.3 Modes de requête

- **DepartAfter** : `from`, `to`, `datetime` = départ au plus tôt → arrive le plus tôt possible.
- **ArriveBy** : `from`, `to`, `datetime` = arrivée au plus tard → quitte le plus tard possible.
- **RangeQuery / départ-dans-une-fenêtre** (v2) : liste de trajets alternatifs sur une plage horaire.

### 6.4 Spécifications de détail

- **Trajets de nuit** : les trips passant minuit doivent être chaînables (un train partant à 23:50 arrivant à 00:20). Limite de recherche : ne pas chercher au-delà de ~24–36 h depuis l'heure de départ pour éviter la boucle infinie.
- **Correspondance minimum** : respecter le temps de correspondance de la gare (§5.3). Un changement n'est valable que si `arrivée_leg1 + min_correspondance ≤ départ_leg2`.
- **Récupération du chemin** : mémoriser, pour chaque arrêt, le trip emprunté et l'arrêt précédent, pour reconstruire les étapes du voyage (backpointer).
- **Contrainte forte (non négociable)** : **tous les legs d'un itinéraire doivent être des trips TER** (whitelist §4.2). Le moteur doit être construit sur le graphe filtré : il est *structurellement* impossible de sortir un TGV/Intercités.

### 6.5 Formats de sortie (objet Journey)

```json
{
  "journeys": [
    {
      "departure": "2026-08-10T07:34:00+02:00",
      "arrival": "2026-08-10T12:04:00+02:00",
      "duration_min": 270,
      "transfers": 1,
      "legs": [
        {
          "type": "train",
          "line": "K7",
          "line_name": "Paris - Lyon P D",
          "vehicle_label": "…",
          "from": { "stop_area_id": "OCE87686006", "name": "Paris Gare de Lyon", "time": "2026-08-10T07:34:00+02:00" },
          "to":   { "stop_area_id": "OCE87713040", "name": "Dijon", "time": "2026-08-10T10:33:00+02:00" }
        },
        {
          "type": "train",
          "line": "C11",
          "line_name": "Dijon-Besançon",
          "vehicle_label": "…",
          "from": { "stop_area_id": "OCE87713040", "name": "Dijon", "time": "2026-08-10T11:09:00+02:00" },
          "to":   { "stop_area_id": "OCE87718007", "name": "Besançon Viotte", "time": "2026-08-10T12:04:00+02:00" }
        }
      ]
    }
  ]
}
```

## 7. API REST

### 7.1 Principes

- Framework : **FastAPI** (Python), documentation auto générée (Swagger `/docs`).
- Format : JSON, UTF-8, champs explicites et typés (pas d'abréviations).
- Stateless : l'API ne garde pas d'état de session.
- CORS ouvert (pour le site web et l'app).
- Versionnage : préfixe `/v1/`.

### 7.2 Endpoints

```
GET /v1/stations/search?q=<texte>&limit=<n>
    → autocomplete gares (nom normalisé, coordonnées, alias)

GET /v1/journeys
    ?from=<stop_area_id ou coordonnées>
    &to=<stop_area_id ou coordonnées>
    &date=YYYY-MM-DD
    &time=HH:MM
    &datetime_represents=departure|arrival     (défaut: departure)
    &max_transfers=0..6                         (défaut: 6)
    &vehicle=all|train_only                    (défaut: all)
    &count=<nb max de trajets retournés>       (défaut: 5)
    → { journeys: [Journey] }                   (§6.5)

GET /v1/health
    → { status: "ok", data_date: "2026-08-09" }
```

### 7.3 Contrats d'erreur

- `from`/`to` introuvable → `404` avec `{ error: { code: "STATION_NOT_FOUND", message: "…", suggestions: ["…"] } }`.
- `date` invalide ou hors plage de données → `400`.
- Aucun trajet trouvé → `200` avec `{ journeys: [] }` (pas d'erreur).

### 7.4 Exemple canonique (à tester impérativement)

```
GET /v1/journeys?from=OCE87686006&to=OCE87718007
    &date=2026-08-10&time=07:00&max_transfers=6
→ doit retourner au moins un trajet Paris Gare de Lyon → Dijon → Besançon Viotte, 1 correspondance.
```

⚠️ L'API doit **rejeter les dates hors plage** (validité du GTFS, cf. §3.1) avec une
erreur 400, et **résoudre la circulation à la date demandée** (§4.5).

## 8. Interface web

### 8.1 Stack recommandée

- **Next.js / React** (rendu SSR pour le SEO des pages « itinéraires »).
- Composants : Tailwind CSS pour le style.
- Carte optionnelle (Leaflet/MapLibre) en v2.

### 8.2 Écrans v1

1. **Accueil / recherche** :
   - Champs « De » et « À » avec autocomplete (endpoint `/v1/stations/search`).
   - Date + heure, sens (départ / arrivée).
   - Filtre « Trains uniquement » (les correspondances sont proposées automatiquement, jusqu'à 6).
   - Bouton rechercher.
2. **Résultats** :
   - Liste des trajets Pareto triés par heure de départ.
   - Pour chaque trajet : heure départ/arrivée, durée, nombre de correspondances, lignes.
   - Badge clair « Train TER » / « Car TER » par étape.
   - Aucun TGV/Intercités affiché, jamais.
3. **Détail d'un trajet** :
   - Timeline des étapes (gare, heure départ/arrivée, ligne, voiture).
   - Note explicite : « Ce trajet ne comporte que des TER ».
4. **Page station** (v2) : prochains départs TER de la gare.

### 8.3 Accessibilité & mobile-first

- Le site doit être utilisable sur mobile dès v1 (responsive). L'app native suivra (T9).

## 9. Application mobile

- v2.1 : **PWA** (le site web en mode hors-ligne partiel, installable) — coût minimal.
- v2.2 : app native **React Native** (iOS + Android) réutilisant le code de l'UI web et l'API.
- Fonctionnalités : recherche, résultats, détail, favoris d'itinéraires (stockage local), alerte de correspondance (v3).

## 10. Temps réel (v2)

- Intégration **GTFS-RT Trip Updates** : appliquer retards/suppressions sur le graphe en mémoire (mise à jour toutes les ~2 min, horizon 60 min).
- Afficher dans l'UI : « retard 12 min », « supprimé », correspondance manquée → proposer une alternative.
- **Service Alerts** (perturbations) : bandeau d'information.
- Sépare en Tâche T8, ne bloque pas v1.

**✅ Livré (T8, 11/08/2026).** Détails d'implémentation dans `walkthrough.md` §19–22 : flux Trip Updates (retards ≥ 0, suppressions CANCELED, mapping trip_id/stop, snapshot), flux Service Alerts (`sncf-gtfs-rt-service-alerts`, périodes actives, cibles stop `StopArea:OCE<uic8>` / numéro de train `OCESN?xxxF`, alertes générales comptées mais non affichées par trajet), API (`use_realtime`, `connection_risks`, `alerts` par trajet, section `realtime.alerts` dans `/v1/health`), UI (badges +X min, risque de correspondance, alternative +30 min, bandeau ⚠). Correctif 11/08 : retards/suppressions **datés par la date de service** (`TripDescriptor.start_date`) — sans datation, un retard du jour s'affichait aussi à J+2, car le suffixe daté du `trip_id` SNCF n'est pas sa date de circulation (§21).

## 11. Monétisation

**Décision : l'outil est gratuit pour les utilisateurs.** Aucun paywall, aucun compte payant.

Pistes différées (à étudier après l'adoption) :
- **Affiliation billetterie** : redirection vers SNCF Connect / Trainline (programme partenaire) au moment de l'achat → commission sur ventes générées.
- **B2B / white-label** : vendre l'API de recherche TER à des acteurs locaux (offices de tourisme, hôtels, applis de mobilité régionales).
- **Sponsoring régional** : mise en avant non intrusive de contenus des Régions.

> Contrainte : la **gratuité et la neutralité des résultats** sont des valeurs produit. Toute option commerciale devra préserver l'impartialité du classement.

## 12. Licence et obligations

- Données SNCF : **ODbL** + **Conditions Particulières d'utilisation** (voir les pages du jeu de données sur `transport.data.gouv.fr` et `data.gouv.fr`).
- Ce que cela implique :
  - Attribution obligatoire (« Données : SNCF Open Data, licence ODbL ») visible dans le site/app.
  - Les **bases de données dérivées** restent sous ODbL (partage à l'identique pour la donnée).
  - L'**utilisation commerciale est permise** (donc l'affiliation plus tard est licite).
  - Le **code source** du logiciel n'est pas couvert par cette obligation : il nous appartient.
  - Vérifier la page exacte des conditions d'utilisation lors de la mise en production (elles évoluent).

## 13. Risques et points d'attention

| Risque | Mitigation |
|---|---|
| `transfers.txt` vide → temps de correspondance approximatifs | Config `interchange.yaml` par gare, calibrée sur les grandes gares ; défaut 5 min |
| Certaines gares TER = arrêts de TGV (ex. Besançon Franche-Comté TGV) | Sans impact : filtre au niveau `trip` (§4.3) |
| Gares multiples à Paris | v1 : gares distinctes + arcs de marche configurables (§5.3) |
| Données théoriques uniquement (pas de retards) | Annoncé explicitement dans l'UI ; GTFS-RT en v2 (T8) |
| Couverture limitée à l'offre SNCF nationale | En v1 suffisant ; les GTFS régionaux plus fins peuvent être fusionnés en v3 |
| Mise à jour des horaires | Pipeline quotidien de re-téléchargement + rechargement du graphe sans downtime |
| Horaires du lendemain / services de nuit | Normalisation > 24h (§5.2) + limite de fenêtre de recherche (§6.4) |
| **Lignes « directes » au service irrégulier** (ex. le K7 Paris→Dijon ne circule pas tous les jours) | Résoudre **toujours** la circulation via `calendar_dates` à la date demandée ; jamais supposer une fréquence (§4.5) |
| `route_short_name` partagé entre plusieurs lignes physiques (ex. « K7 ») | Identifier les lignes par `route_id` ; `short_name` = simple étiquette (§4.3) |

## 14. Découpage en tâches pour les agents IA

Chaque tâche est un **lot livrable autonome**. L'ordre série T1→T4 est bloquant ; ensuite T5→T6 sont parallélisables ; T7/T8/T9 viennent après.

---

### TÂCHE T1 — Pipeline de données + filtre TER

**Objectif :** télécharger le GTFS SNCF, le valider, produire un **GTFS-TER propre** (uniquement l'offre TER).

**Livrables :**
- Script `download.py` : télécharge `Export_OpenData_SNCF_GTFS_NewTripId.zip`, vérifie la taille/le hash, versionne dans `data/raw/`.
- Script `filter_ter.py` : implémente §4 (whitelist des modes, propagation du filtre, sortie `data/ter/gtfs_ter.zip`).
- Script `validate_ter.py` (tests de régression) :
  - ✅ Aucun `stop_id` contenant `OCETGV INOUI`, `OCEOUIGO`, `OCEINTERCITES`, `OCEICE`, `OCELyria` ne subsiste.
  - ✅ La route `K7 | Paris - Lyon P D` (TER) est bien présente.
  - ✅ La route `611A | Paris - Besançon Viotte` est bien absente (c'est un TGV).
  - ✅ Toutes les routes restantes ont au moins un trip `OCETrain TER` / `OCECar TER`.
  - ✅ Un rapport de comptage (nb routes/trips/stop_times avant/après) est généré en `reports/`.
- Script `connectivity_check.py` (outil de validation) : vérifie qu'un couple de gares
  est relié en TER avec ≤ 3 changements à une date donnée (implémentation RAPTOR
  simplifiée — sera remplacé par le moteur de T3). Voir `walkthrough.md` §5.

**Critères d'acceptation :** les 5 tests passent ; le GTFS-TER se dézippe proprement ;
le cas canonique `Paris Gare de Lyon → Besançon Viotte` est trouvé en 1 changement
(le 10/08/2026) ; exécution documentée dans `README.md` et `walkthrough.md`.

**Dépendances :** aucune.

**Stack :** Python ≥3.10, dépendances minimales (`requests`, stdlib `zipfile`/`csv`).

---

### TÂCHE T2 — Builder de graphe

**Objectif :** transformer le GTFS-TER en structures en mémoire prêtes pour le routage.

**Livrables :**
- Parser des fichiers GTFS (§5).
- Normalisation des horaires en minutes absolues, y compris > 24h (§5.2).
- Résolution de la circulation d'un trip à une date via `calendar_dates` (§5.2).
- Construction des StopArea / StopPoint et du **graphe de transferts** (§5.3) avec le fichier `interchange.yaml` (défaut 5 min, valeurs par gare).
- Arcs de marche intra-Paris optionnels (fichier de config `paris_links.yaml`).
- Index de recherche des gares avec normalisation des accents et alias (§5.4).
- Sérialisation du graphe en cache binaire (ex. `data/graph.bin`, via `pickle` ou un format dédié) pour un chargement rapide.
- CLI : `build_graph.py --input data/ter/gtfs_ter.zip --output data/graph.bin`.

**Critères d'acceptation :**
- Pour la date du 10/08/2026, un trip de `K7` dessert bien Paris Gare de Lyon → Dijon (et un `C11` dessert Dijon → Besançon Viotte).
- Les correspondances à Dijon sont calculables (même StopArea, temps ≥ config).
- Le fichier de cache se charge en < 2 s.

**Dépendances :** T1.

---

### TÂCHE T3 — Moteur de calcul d'itinéraires (McRAPTOR)

**Statut : ✅ LIVRÉ et VALIDÉ le 10/08/2026** — cf. `walkthrough.md` §8.

**Objectif :** implémenter le calcul d'itinéraires multi-critères Pareto avec limite de 3 correspondances (§6).

**Livrables :**
- Implémentation **McRAPTOR** : rounds, marquage des arrêts mis à jour, balayage des trips par route, backpointers, reconstruction des legs.
- Modes **DepartAfter** et **ArriveBy**.
- Limite `max_transfers` (0–6).
- Gestion des services de nuit / fenêtre de recherche (§6.4).
- Filtre `vehicle=all|train_only` (exclut `OCECar TER` et `OCETramTrain` si demandé).
- Objet `Journey` sérialisable au format JSON de §6.5.
- Tests unitaires : trajet direct, 1 correspondance, 2 correspondances, 3 correspondances, pas de solution, nuit.

**Critères d'acceptation :**
- Sur le graphe construit par T2, la requête canonique `Paris Gare de Lyon → Besançon Viotte` retourne un trajet en **1 correspondance via Dijon**. ✅ (K7 07:34 → Dijon 10:33, C11 11:09 → Besançon 12:04)
- **Aucun** résultat ne contient de trip non-TER (par construction). ✅
- Temps de réponse < 100 ms par requête (mesuré). ✅ (76 ms/requête à chaud ; 100 requêtes, 3 dates)

**Validation :** 14 tests unitaires OK (`tests/test_raptor.py`) ; parité des arrivées
avec `connectivity_check` sur **16/16 couples** en comparaison train-only (le raptor
applique en plus les arcs de marche inter-gares, §5.3, que l'oracle ignore — les
écarts restants sont ce cas-là, documenté). Bug T2 découvert et corrigé au passage
(`routes_by_stop` ne listait la route qu'au premier arrêt de ses trips) avec test de
non-régression sur **tous** les (trip, arrêt). Bugs corrigés dans le moteur : union
des arrêts par route (terminus différents selon les trips), suivi des améliorations
de `new_marked` dans un round, legs de marche inter-gares (reconstruction cohérente
en temps, plafond `MAX_WALK_LEGS = 2`).

**Dépendances :** T2.

---

### TÂCHE T4 — Golden tests de bout en bout

**Statut : ✅ LIVRÉ et VALIDÉ le 10/08/2026** — cf. `walkthrough.md` §9.

**Objectif :** valider la justesse des résultats sur des trajets réels vérifiés manuellement.

**Livrables :**
- Script de tests `tests/golden_tests.py` avec une table de cas vérifiés (trajets TER connus) :
  - Paris Gare de Lyon → Besançon Viotte : **1 changement à Dijon** (cas référence, §2, horaires réels du 10/08/2026 : K7 07:34 → Dijon 10:33, C11 11:09 → Besançon 12:04).
  - Paris Bercy → Dijon : direct `K7` certains jours, sinon 1 changement via Laroche-Migennes (`P25` + `K7`).
  - Paris → Vittel : direct TER (route `K6 | PARIS - VITTEL`).
  - Toulouse → Clermont-Ferrand : TER via Brive (à vérifier sur les données courantes).
  - Un cas à 0 correspondance, un cas à 2, un cas à 3.
  - Un cas sans solution (ex. une destination non desservie en TER) → `{ journeys: [] }`.
- Pour chaque cas : vérifier la cohérence (heures croissantes, correspondances respectées, gares correctes).
- ⚠️ Les golden tests doivent être **datés** : un trajet « direct » peut ne pas circuler
  tous les jours (cf. §4.5). Choisir une date où le service est vérifié dans le GTFS.

**Critères d'acceptation :** tous les golden tests passent ; les horaires des trajets
retournés correspondent aux horaires du GTFS (contrôle croisé indépendant).

**Validation :** 16 tests OK (`tests/test_raptor.py` + `tests/golden_tests.py`).
Table datée : 11 cas « lundi 10/08/2026 », 2 cas « dimanche 16/08/2026 » dont le direct
Paris→Vittel (K6 N840451 08:21 → 12:42, **absent le lundi**) et 1 cas ArriveBy. Parité
**16/16** avec `connectivity_check` en comparaison train-only ; les itinéraires piétons
inter-gares (Bercy→GDL→Est, etc.) sont une capacité en plus du moteur que l'oracle
n'a pas. Contrôles de cohérence systématiques : heures strictement croissantes,
continuité des gares, `min_transfer` respecté, `transfers == len(legs) - 1`.

**Dépendances :** T3.

---

### TÂCHE T5 — API REST FastAPI

**Statut : ✅ LIVRÉ et VALIDÉ le 10/08/2026** — cf. `walkthrough.md` §10.

**Objectif :** exposer le moteur via une API HTTP documentée (§7).

**Livrables :**
- Application FastAPI avec endpoints §7.2.
- Gestion des erreurs conforme §7.3.
- Chargement du graphe au démarrage + endpoint `/v1/health`.
- Tests d'intégration (httpx/testclient) couvrant les contrats.
- `README.md` d'utilisation de l'API avec curl.

**Critères d'acceptation :** l'exemple canonique (§7.4) passe via HTTP ; Swagger `/docs` fonctionnel ; les erreurs respectent les contrats.

**Validation :** 17 tests d'intégration OK (`tests/test_api.py`). Exemple canonique
§7.4 vérifié en HTTP réel (uvicorn) : `OCE87686006 → OCE87718007` le 10/08/2026 à
07:00 → 1 correspondance, K7 07:34 → Dijon 10:33 → C11 11:09 → Besançon 12:04.
Contrats d'erreur vérifiés : 404 `STATION_NOT_FOUND` (suggestions), 400
`INVALID_DATE` (hors plage + format) et `INVALID_TIME`, 422 paramètres hors
domaine (FastAPI), 200 avec `journeys: []` sans solution. `from/to` accepte un
`stop_area_id` nu ou préfixé `StopArea:`, des coordonnées « lat,lon » (plus
proche), un nom de gare, un groupe (« Paris »). Arrivées jour+1 correctement
datées (Paris → Grenoble 07:00 → 2026-08-11T00:31). Swagger `/docs` OK.

**Dépendances :** T3.

---

### TÂCHE T6 — Site web (MVP)

**Statut : ✅ LIVRÉ et VALIDÉ le 10/08/2026** — cf. `walkthrough.md` §11.

**Objectif :** l'interface de recherche sur navigateur (§8).

**Livrables :**
- Projet Next.js/React : page d'accueil (recherche), page résultats, page détail.
- Autocomplete des gares (appel `/v1/stations/search`).
- Affichage des trajets Pareto ; badge « Train TER » / « Car TER » ; filtre « Trains uniquement » ; limite de correspondances.
- Mobile-first, accessibilité de base.
- Attribution ODbL visible (§12).

**Critères d'acceptation :** le parcours complet (chercher Paris → Besançon, trouver le trajet TER 1 changement à Dijon, voir le détail) fonctionne de bout en bout contre l'API T5. Aucun TGV/Intercites n'est affichable.

**Dépendances :** T5.

**Note de stack :** la recommandation §8.1 (Next.js/React) est différée — le MVP est une
**SPA statique** (`web/`, HTML/CSS/JS vanilla) servie par FastAPI (StaticFiles à la racine,
les routes `/v1/*` restant prioritaires). Aucun build, testable de bout en bout avec le
TestClient, migration possible vers Next.js/React en T7 (PWA) si le SEO/SSR le justifie.

**Validation :** 4 tests web + 17 API = 21 tests OK (`tests/test_api.py`). Parcours complet
vérifié en HTTP réel (uvicorn) : `/` sert la page, autocomplete, puis
`/v1/journeys?from=Paris Gare de Lyon&to=Besançon Viotte&date=2026-08-10&time=07:00`
→ 1 correspondance (K7 → C11 via Dijon). Écrans §8.2 : formulaire (De/À, date, heure,
sens, correspondances 0-3, « Trains uniquement »), liste Pareto (heures, durée, lignes,
badges Train TER / Car TER / TramTrain / Marche, badge « +1j » si arrivée le lendemain),
détail en timeline, note « Ce trajet ne comporte que des TER », attribution ODbL dans le
pied de page. Mobile-first + aria (autocomplete, alerts).

---

### TÂCHE T7 — Application mobile (PWA puis native)

**Statut : ✅ LIVRÉ et VALIDÉ le 10/08/2026** — v2.1 (PWA, cf. `walkthrough.md` §14) et v2.2 (app native, cf. §16).

**Objectif :** rendre le service utilisable sur mobile comme application installable, puis native.

**Livrables :**
- v2.1 : transformation du site en **PWA** (manifest, service worker, cache hors-ligne partiel).
- v2.2 : app native (iOS + Android) via **Capacitor** — réutilise la SPA web (même codebase, API T5 appelée sur `https://ter.zvz.fr`), shell natif généré par `npx cap add android`, build APK en CI **GitHub Actions** (`.github/workflows/build-apk.yml`, modèle du repo pressscraper : `cap sync android` + `gradlew assembleDebug`, Java 21).

**Critères d'acceptation (v2.1) :** installable sur mobile, recherche fonctionnelle hors-ligne (cache des résultats récents).
**Validation v2.1 :** SPA transformée en PWA — `manifest.webmanifest` (display standalone, thème `#1a3a8f`, icônes 192/512 + maskable + apple-touch-icon 180), `sw.js` (precache du shell, cache API borné à 100 entrées : network-first pour `/v1/journeys`, stale-while-revalidate pour `/v1/health` + `/v1/stations/search` et les assets), inscription du SW dans `app.js`. Assets servis avec les bons MIME (`application/manifest+json`, `text/javascript`), 21 tests d'intégration toujours OK, vérifié en prod sur https://ter.zvz.fr.
**Critères d'acceptation (v2.2) :** app publiée sur les stores, même parcours que le site. APK debug produit par la CI ✅ (artefact `ter-finder-debug-apk`, ~3,7 Mo).
**Validation v2.2 :** repo GitHub `muarf/PlanTER` ; workflow `Build Android APK` vert sur `main` (push + dispatch). Le WebView détecte Capacitor (`window.Capacitor`) et bascule l'API sur `https://ter.zvz.fr` (`API_BASE` dans `web/app.js`). Publication stores : reste à signer (keystore) et à configurer les stores.
**Note stack :** React Native (prévu initialement) remplacé par **Capacitor** : la SPA web existante est enveloppée telle quelle, un seul codebase à maintenir, workflow CI identique à `pressscraper`.

**Dépendances :** T5.

---

### TÂCHE T8 — Temps réel GTFS-RT (v2)

**Statut : ✅ LIVRÉ et VALIDÉ le 11/08/2026** — module `src/gtfs_rt.py` (parsing des deux flux + poller daemon 2 min), application des retards/suppressions dans le moteur McRAPTOR, `use_realtime` sur `/v1/journeys`, section `realtime` dans `/v1/health`, badges de retard + alerte « correspondance manquée » avec alternative dans l'UI. **Service Alerts inclus** : parsing + mapping cibles (stop/numéro de train), `alerts` pertinentes par trajet, bandeau ⚠ dans l'UI, compteur dans `/v1/health`. Testé en prod (`ter.zvz.fr`). **Correctif 11/08/2026 :** retards/suppressions datés par la date de service réelle (`TripDescriptor.start_date`) — le suffixe daté du `trip_id` SNCF n'est pas la date de circulation (un même trip_id circule sur plusieurs jours), sans datation un retard du jour s'affichait aussi à J+2. Voir `walkthrough.md` §21.

**Objectif :** intégrer retards, suppressions et alertes (§10).

**Livrables :**
- Polling périodique de `sncf-gtfs-rt-trip-updates` et `sncf-gtfs-rt-service-alerts` (toutes les ~2 min).
- Application des retards/suppressions sur le graphe en mémoire (recalcul d'itinéraires avec horaires réels).
- Endpoint `/v1/journeys` avec paramètre `use_realtime` (défaut `true` — les retards réels sont la réalité affichée, le paramètre reste pour compatibilité).
- Affichage UI : retards, suppressions, « correspondance manquée » + proposition d'alternative, bandeau perturbations.

**Critères d'acceptation :** avec le flux réel branché, un trajet dont le train est retardé de 12 min s'affiche avec le retard ; un train supprimé ne génère plus d'itinéraire (ou génère une alternative) ; une perturbation ciblant une gare ou un train du trajet s'affiche en bandeau.

**Dépendances :** T3, T5.

---

### TÂCHE T9 — Monétisation par affiliation (étude différée)

**Statut : PoC ✅ (v1 liens Trainline) le 10/08/2026** — cf. `walkthrough.md` §17. Programme d'affiliation proprement dit : à étudier.

**Objectif :** préparer la redirection d'achat vers un partenaire de billetterie, sans changer la gratuité ni la neutralité (§11).

**Livrables (études + PoC) :**
- Récupérer les conditions des programmes partenaires SNCF Connect / Trainline (commission, URLs de deep-link avec paramètres gare/date/heure).
- PoC : bouton « Voir les horaires & acheter sur Trainline » sur la page détail, générant une URL pré-remplie. **Livré :** module `src/trainline.py` (cartographie `uic8` → slug Trainline depuis le repo officiel `trainline-eu/stations-studio`/`stations.csv`, 4000 gares mappées) ; l'API expose `trainline_slug` dans `/v1/stations/search` et un objet `booking.{provider,url}` par leg ferroviaire (`/book/results?origin={slug}&destination={slug}&outbound_date=…&outbound_time=…`, un billet TER par segment, marches exclues) ; bouton affiché quand le mapping existe. Format validé le 11/08/2026 en navigateur headless : seul le **slug** est accepté (les codes `sncf_id` FR… et les ids numériques sont rejetés par Trainline). Sans paramètre d'affiliation pour l'instant.
- Ne jamais modifier l'ordre des résultats selon la commission.

**Critères d'acceptation :** le lien d'affiliation est généré correctement ; le classement reste inchangé.

**Dépendances :** T6.

---

## 15. Roadmap

| Phase | Contenu | Critère de sortie |
|---|---|---|
| **Phase 1 — Moteur** | T1 → T2 → T3 → T4 | Trajets TER corrects vérifiés (golden tests) |
| **Phase 2 — API** | T5 | API stable et documentée |
| **Phase 3 — Web** | T6 | MVP web en ligne, gratuit |
| **Phase 4 — Mobile** | T7 | PWA puis apps stores |
| **Phase 5 — Temps réel** | T8 | Retards et suppressions intégrés |
| **Phase 6 — Monétisation** | T9 | Affiliation sans altérer la neutralité |

---

## Annexe A — Références techniques

- GTFS Reference : https://developers.google.com/transit/gtfs/reference
- Jeu de données SNCF (transport.data.gouv.fr) : https://transport.data.gouv.fr/datasets/horaires-sncf
- Téléchargement GTFS : `https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip`
- GTFS-RT Trip Updates : `https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates`
- Papier RAPTOR : Delling, Pajor, Werneck, ALENEX 2012.
- Licence ODbL : https://opendatacommons.org/licenses/odbl/1.0/
- Navitia (à titre de référence d'architecture, pas d'implémentation) : https://www.navitia.io

## Annexe B — Glossaire

- **TER** : Train Express Régional (offre régionale SNCF).
- **Car TER** : autocar exploité dans le cadre de l'offre TER.
- **Tram-train** : service ferroviaire léger régional (ex. Mulhouse, Nantes).
- **StopArea / StopPoint** : gare / arrêt physique (nomenclature GTFS).
- **Trip** : course (un train concret à une date/heure).
- **Pareto-optimal** : solution dont aucun critère ne peut être amélioré sans dégrader un autre.
