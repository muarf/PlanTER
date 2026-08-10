#!/usr/bin/env python3
"""T1 — download.py : télécharge le GTFS SNCF, le vérifie et le versionne.

Usage :
    python -m src.download                     # télécharge le dernier GTFS
    python -m src.download --url <URL>         # URL alternative
    python -m src.download --out <dir>         # répertoire de sortie
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from src import config  # noqa: E402

# Répertoires par défaut : racine du projet.
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW_DIR = ROOT / "data" / "raw"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_zip(path: Path) -> list[str]:
    """Vérifie que le fichier est un zip GTFS exploitable.

    Retourne la liste des fichiers manquants (vide si tout est bon).
    """
    missing: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            for required in config.REQUIRED_GTFS_FILES:
                if required not in names:
                    missing.append(required)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Fichier invalide (pas un zip) : {path}") from exc
    return missing


def download(url: str, out_dir: Path) -> tuple[Path, dict]:
    """Télécharge le GTFS, le vérifie et le versionne dans out_dir.

    Retourne (chemin du zip versionné, métadonnées).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = out_dir / f"gtfs_{stamp}.zip"

    print(f"[download] {url}")
    resp = requests.get(url, timeout=300, stream=True)
    resp.raise_for_status()

    size = 0
    with target.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
            size += len(chunk)

    if size < config.MIN_GTFS_SIZE_BYTES:
        target.unlink(missing_ok=True)
        raise ValueError(
            f"Téléchargement suspect : {size} octets (minimum attendu "
            f"{config.MIN_GTFS_SIZE_BYTES})"
        )

    missing = _validate_zip(target)
    if missing:
        target.unlink(missing_ok=True)
        raise ValueError(
            f"GTFS incomplet : fichiers manquants {missing}"
        )

    checksum = _sha256(target)
    metadata = {
        "url": url,
        "downloaded_at": stamp,
        "size_bytes": size,
        "sha256": checksum,
        "filename": target.name,
    }

    # Versionnage + pointeur "latest" consommé par filter_ter.
    manifest_path = out_dir / "manifest.json"
    history: list[dict] = []
    if manifest_path.exists():
        history = json.loads(manifest_path.read_text()).get("history", [])
    history.append(metadata)
    manifest_path.write_text(
        json.dumps({"history": history}, ensure_ascii=False, indent=2)
    )

    latest = out_dir / "latest.zip"
    if latest.exists():
        latest.unlink()
    latest.hardlink_to(target)

    print(f"[download] OK -> {target} ({size} octets, sha256 {checksum[:12]}…)")
    print(f"[download] latest  -> {latest}")
    return target, metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Télécharge le GTFS SNCF")
    parser.add_argument("--url", default=config.GTFS_URL)
    parser.add_argument("--out", type=Path, default=DEFAULT_RAW_DIR)
    args = parser.parse_args(argv)
    try:
        download(args.url, args.out)
    except Exception as exc:  # noqa: BLE001
        print(f"[download] ERREUR : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
