# Plan d'implémentation — T12 : Tarification et Réductions TER

Ce document détaille la stratégie technique pour implémenter l'estimation du tarif plein et le calcul des tarifs réduits avec cartes de réduction dans **planTER**.

---

## 1. Cartographie des gares par région

Pour appliquer les bons tarifs et réductions, le moteur doit connaître la région de chaque gare (BFC, Grand Est, Normandie, etc.).

### Action
- Créer un script `scripts/build_station_regions.py` qui télécharge le JSON d'exportation officiel de la SNCF (`liste-des-gares`) depuis la plateforme SNCF Open Data.
- Mapper chaque département/commune à une région administrative française.
- Exporter un dictionnaire statique `config/station_regions.json` contenant la relation `{ "uic8": "CODE_REGION" }` (ex: `{"87713040": "BFC"}`).
- Ce fichier sera commité dans le dépôt pour être accessible en local sans appel réseau au runtime.

---

## 2. Moteur de tarification (pricing.py)

Création d'un module autonome `src/pricing.py` contenant les règles et formules de calcul.

### Logique de calcul (`calculate_fare(journey, card_ids)`)
1. **Trajet mono-régional (ex: Paris ➔ Dijon ➔ Besançon)** :
   Si tous les legs ferroviaires du trajet sont opérés par la même région organisatrice (détectée via `config/station_regions.json`), on applique le barème kilométrique dégressif de cette région sur la **distance totale cumulée**.
2. **Trajet pluri-régional (ex: Lille ➔ Amiens ➔ Rouen)** :
   Si le trajet traverse plusieurs régions sans accord tarifaire, on calcule le prix de chaque tronçon séparément en utilisant la distance de chaque leg, puis on les additionne.
3. **Application des cartes de réduction** :
   Pour chaque carte présente dans `card_ids`, appliquer les règles définies (ex: réduction de 50 %, 30 %, ou tarifs fixes par paliers kilométriques) sur la portion correspondante.

---

## 3. Exposition de l'API (api.py)

Mise à jour du point d'entrée pour fournir les tarifs.

### Modifications
- Ré-activer la prise en compte du paramètre `cards` (chaîne séparée par des virgules) dans `GET /v1/journeys`.
- Ajouter deux champs dans l'objet de réponse de chaque trajet :
  * `price_normal_eur` : prix estimé au tarif normal.
  * `price_reduced_eur` : prix estimé après application des cartes de réduction sélectionnées.

---

## 4. Interface Web (index.html & app.js)

Ré-intégration visuelle du sélecteur de cartes.

### Modifications
- Ajouter un menu multi-sélecteur des cartes de réduction dans le formulaire de recherche (chargé dynamiquement à partir de `/v1/cards`).
- Transmettre les cartes sélectionnées dans les paramètres de la requête de recherche d'itinéraires.
- Afficher les tarifs estimés (plein tarif et tarif réduit) dans les résultats de recherche et la timeline de détails.
