"""Proof-of-Work (PoW) anti-abus — sans logs, sans IP.

Principe : le client doit résoudre un puzzle SHA256 avant d'appeler l'API.
Le défi est aléatoire, jetable (TTL 60s) et stocké uniquement en RAM.

Difficulty adaptative : monte si le CPU est chargé, descend sinon.
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional


_DEFAULT_DIFFICULTY = 4
_SALT_TTL_S = 60
_CLEANUP_INTERVAL_S = 10
_max_difficulty = 6


class PoWEngine:
    """Moteur PoW léger, sans persistance, sans logs."""

    def __init__(self, difficulty: int = _DEFAULT_DIFFICULTY) -> None:
        self._challenges: dict[str, dict] = {}
        self._difficulty = difficulty
        self._last_cleanup = time.monotonic()
        self.enabled = True

    @property
    def difficulty(self) -> int:
        return self._difficulty

    @difficulty.setter
    def difficulty(self, v: int) -> None:
        self._difficulty = max(2, min(v, _max_difficulty))

    def generate_challenge(self) -> dict:
        """Génère un défi aléatoire avec TTL."""
        self._maybe_cleanup()
        salt = os.urandom(16).hex()
        challenge = {
            "salt": salt,
            "difficulty": self._difficulty,
            "ts": time.monotonic(),
        }
        self._challenges[salt] = challenge
        return {"salt": salt, "difficulty": self._difficulty}

    def verify(self, salt: str, nonce: str, difficulty: Optional[int] = None) -> bool:
        """Vérifie la solution PoW. Retourne True si valide."""
        if difficulty is None:
            difficulty = self._difficulty

        # Vérifier que le salt existe et n'a pas expiré
        ch = self._challenges.get(salt)
        if ch is None:
            return False
        age = time.monotonic() - ch["ts"]
        if age > _SALT_TTL_S:
            del self._challenges[salt]
            return False

        # La difficulty demandée ne peut pas dépasser celle du défi
        if difficulty > ch["difficulty"]:
            return False

        # Vérifier le hash
        payload = f"{salt}:{nonce}".encode()
        h = hashlib.sha256(payload).hexdigest()
        prefix = "0" * difficulty
        return h.startswith(prefix)

    def _maybe_cleanup(self) -> None:
        """Nettoie les défi expirés (toutes les 10s)."""
        now = time.monotonic()
        if now - self._last_cleanup < _CLEANUP_INTERVAL_S:
            return
        self._last_cleanup = now
        expired = [s for s, c in self._challenges.items() if now - c["ts"] > _SALT_TTL_S]
        for s in expired:
            del self._challenges[s]


def solve(salt: str, difficulty: int) -> Optional[str]:
    """Résout un défi PoW (côté client). Retourne le nonce ou None."""
    prefix = "0" * difficulty
    for nonce in range(1_000_000_000):
        payload = f"{salt}:{nonce}".encode()
        h = hashlib.sha256(payload).hexdigest()
        if h.startswith(prefix):
            return str(nonce)
    return None
