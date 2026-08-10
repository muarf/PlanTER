"""Fonctions GTFS partagées (lecture/écriture CSV robustes).

Le GTFS est du CSV, mais certains champs peuvent contenir des virgules entre
guillemets. On passe toujours par le module `csv` de la stdlib, jamais par un
découpage naïf sur ",".
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from src import config

# Type : rows = liste de dicts
GTFSRows = list[dict[str, str]]


def read_csv_text(text: str) -> tuple[list[str], GTFSRows]:
    """Lit un contenu CSV texte et retourne (headers, rows en dicts)."""
    reader = csv.DictReader(io.StringIO(text))
    rows: GTFSRows = [dict(r) for r in reader]
    return list(reader.fieldnames or []), rows


def read_zip_file(zf: zipfile.ZipFile, name: str) -> tuple[list[str], GTFSRows]:
    """Lit un fichier CSV depuis un zip GTFS."""
    text = zf.read(name).decode("utf-8-sig")
    return read_csv_text(text)


def write_csv_rows(headers: list[str], rows: GTFSRows) -> str:
    """Sérialise des rows en CSV, en préservant l'ordre des colonnes d'origine."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def write_gtfs_zip(out_path: Path, files: dict[str, str]) -> None:
    """Écrit un zip GTFS depuis un mapping nom de fichier -> contenu texte."""
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def extract_mode_from_stop_id(stop_id: str) -> str | None:
    """Extrait le mode commercial depuis un stop_id SNCF.

    Ex. "StopPoint:OCETrain TER-87713040" -> "OCETrain TER"
    Retourne None si le stop_id ne suit pas le schéma attendu.
    """
    if not stop_id.startswith(config.STOP_ID_PREFIX):
        return None
    rest = stop_id[len(config.STOP_ID_PREFIX):]
    return rest.split("-", 1)[0] if rest else None
