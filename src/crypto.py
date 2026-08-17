"""Chiffrement hybride des requêtes API — AES-256-GCM + RSA-2048-OAEP.

Le payload est chiffré avec un AES-GCM aléatoire, puis la clé AES est
chiffrée avec la clé publique RSA du serveur. Seul le serveur (clé privée
en mémoire) peut déchiffrer.

Format du payload chiffré (base64) :
  [32 bytesclé AES chiffrée RSA] + [12 bytes IV] + [N bytes ciphertext AES-GCM]
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CryptoEngine:
    """Moteur hybride AES-256-GCM + RSA-2048-OAEP.
    Clé RSA régénérée à chaque démarrage (jamais sur disque)."""

    def __init__(self) -> None:
        self._private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self._public_key = self._private_key.public_key()
        self._rsa_key_size_bytes = 2048 // 8  # 256

    def pubkey_pem(self) -> str:
        """Clé publique PEM (servie aux clients)."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def encrypt_b64(self, data: dict) -> str:
        """Chiffre un dict et retourne un base64 (utile pour tests serveur)."""
        plaintext = json.dumps(data, separators=(",", ":")).encode()
        return self._encrypt_bytes_b64(plaintext)

    def _encrypt_bytes_b64(self, plaintext: bytes) -> str:
        # 1. Générer clé AES-256 aléatoire + IV
        aes_key = AESGCM.generate_key(bit_length=256)
        iv = os.urandom(12)

        # 2. Chiffrer le payload avec AES-GCM
        aesgcm = AESGCM(aes_key)
        ciphertext = aesgcm.encrypt(iv, plaintext, None)

        # 3. Chiffrer la clé AES avec RSA-OAEP
        encrypted_key = self._public_key.encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

        # 4. Concaténer : [clé AES chiffrée RSA] + [IV] + [ciphertext AES-GCM]
        payload = encrypted_key + iv + ciphertext
        return base64.b64encode(payload).decode()

    def decrypt_b64(self, payload_b64: str) -> dict:
        """Déchiffre un payload base64 en dict JSON."""
        raw = base64.b64decode(payload_b64)

        # Extraire : [clé AES chiffrée RSA (256 bytes)] + [IV (12 bytes)] + [ciphertext]
        if len(raw) < self._rsa_key_size_bytes + 12:
            raise ValueError("payload trop court")

        encrypted_key = raw[: self._rsa_key_size_bytes]
        iv = raw[self._rsa_key_size_bytes : self._rsa_key_size_bytes + 12]
        ciphertext = raw[self._rsa_key_size_bytes + 12 :]

        # 1. Déchiffrer la clé AES avec RSA-OAEP
        aes_key = self._private_key.decrypt(
            encrypted_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

        # 2. Déchiffrer le payload avec AES-GCM
        aesgcm = AESGCM(aes_key)
        plaintext = aesgcm.decrypt(iv, ciphertext, None)

        return json.loads(plaintext)
