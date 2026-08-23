"""Configuration du pipeline de données PlanTER.

URL du jeu de données global SNCF. Il contient TGV, Intercités et TER :
c'est le seul fichier encore publié depuis l'été 2025 (les jeux sectoriels
TER ont été décommissionnés). Le filtrage TER est donc notre responsabilité.
"""

GTFS_URL = "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip"

# Taille minimale acceptable (en octets) : le zip fait ~4,6 Mo, on se garde
# une marge large pour détecter un téléchargement incomplet.
MIN_GTFS_SIZE_BYTES = 1_000_000

# Fichiers GTFS requis présents dans l'export SNCF.
REQUIRED_GTFS_FILES = [
    "agency.txt",
    "calendar_dates.txt",
    "feed_info.txt",
    "routes.txt",
    "stop_times.txt",
    "stops.txt",
    "trips.txt",
]

# ---------------------------------------------------------------------------
# Modes commerciaux SNCF encodés dans les stop_id :
#     StopPoint:OCE<MODE COMMERCIAL>-<code UIC de la gare>
# Ex. : StopPoint:OCETrain TER-87713040   (train TER à Dijon)
#       StopPoint:OCETGV INOUI-87713040   (TGV Inoui à Dijon)
#
# Le préfixe mode = tout ce qui est entre "StopPoint:OCE" et le premier "-".
# On compare donc sur le jeton SANS le préfixe "OCE"
# (ex. "Train TER", "TGV INOUI", "Car à réservation").
# ---------------------------------------------------------------------------

STOP_ID_PREFIX = "StopPoint:OCE"

# Offre TER : on ne garde QUE ces modes.
WHITELIST_MODES = {
    "Train TER",      # train TER
    "Car TER",        # car TER (offre régionale)
    "TramTrain",      # tram-train régional
    "Train",          # train générique (cas rare)
}

# Modes explicitement exclus (ne jamais les inclure).
BLACKLIST_MODES = {
    "TGV INOUI",
    "OUIGO",
    "INTERCITES",
    "INTERCITES de nuit",
    "ICE",
    "Lyria",
    "Car à réservation",
    "Navette",
}

# Correspondance mode -> type de véhicule exposé dans le graphe / l'UI.
MODE_VEHICLE_TYPE = {
    "Train TER": "train",
    "Car TER": "car",
    "TramTrain": "tram_train",
    "Train": "train",
}


# ---------------------------------------------------------------------------
# Bus régionaux interurbains : préfixe des arrêts bus dans le graphe.
# Les stop_id bus sont préfixés pour éviter les collisions avec les IDs SNCF.
# Ex. : BusStop:UT21:03607 (arrêt bus Mobigo BFC)
# ---------------------------------------------------------------------------
BUS_STOP_PREFIX = "BusStop:"

# Fichier de config des feeds bus régionaux.
BUS_FEEDS_FILE = "config/bus_feeds.json"
