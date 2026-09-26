"""R5: PyNaCl crypto_box (X25519 + XSalsa20-Poly1305, authenticated). The
device keypair is generated on first boot into backend/keys/ (gitignored):
device_private.key never leaves the Pi, device_public.key is what pairing
shares. seal(payload_json, peer_pk) -> {nonce, ciphertext}; open_box(...)
authenticates the sender (doctor messages come from the paired key or they
are rejected). Patient data leaves the device only sealed to the paired key
(invariant 11); the relay stores ciphertext only.

Keys and nonces travel base64 (standard alphabet, padded). code4 is the
pairing's anti-swap check both sides show: the first 4 bytes of
sha256(device_pk_b64 || doctor_pk_b64 || token) as a big-endian integer,
mod 10000, zero-padded to four digits, over the UTF-8 bytes of the base64
strings and the hex token exactly as they travel in the QR."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from nacl.exceptions import CryptoError
from nacl.public import Box, PrivateKey, PublicKey
from nacl.utils import random as nacl_random

from ..config import BACKEND_DIR

KEYS_DIR = BACKEND_DIR / "keys"
PRIVATE_FILE = "device_private.key"
PUBLIC_FILE = "device_public.key"

__all__ = ["CryptoError", "device_keypair", "device_public_key", "seal", "open_box", "code4", "generate_keypair"]


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text.strip(), validate=True)


def device_keypair(keys_dir: Path | None = None) -> PrivateKey:
    """Load the device's private key, generating it on first boot (0600)."""
    keys_dir = keys_dir or KEYS_DIR
    private_path = keys_dir / PRIVATE_FILE
    if private_path.exists():
        if os.stat(private_path).st_mode & 0o077:  # restored from a backup with loose permissions
            os.chmod(private_path, 0o600)
        return PrivateKey(private_path.read_bytes())
    keys_dir.mkdir(parents=True, exist_ok=True)
    key = PrivateKey.generate()
    fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(bytes(key))
    (keys_dir / PUBLIC_FILE).write_text(b64(bytes(key.public_key)) + "\n")
    return key


def device_public_key(keys_dir: Path | None = None) -> str:
    return b64(bytes(device_keypair(keys_dir).public_key))


def generate_keypair() -> tuple[str, str]:
    """(private_b64, public_b64): the browser peer's keypair in tests and the demo."""
    key = PrivateKey.generate()
    return b64(bytes(key)), b64(bytes(key.public_key))


def seal(payload_json: str, peer_pk: str, private_key: PrivateKey | None = None) -> dict[str, str]:
    """Authenticated encryption to the peer: only the holder of peer_pk's private
    key opens it, and only from this device's key does it open on their side."""
    box = Box(private_key or device_keypair(), PublicKey(unb64(peer_pk)))
    nonce = nacl_random(Box.NONCE_SIZE)
    return {"nonce": b64(nonce), "ciphertext": b64(box.encrypt(payload_json.encode("utf-8"), nonce).ciphertext)}


def open_box(nonce: str, ciphertext: str, peer_pk: str, private_key: PrivateKey | None = None) -> str:
    """Raises CryptoError on a tampered box or a box sealed by any key but peer_pk."""
    box = Box(private_key or device_keypair(), PublicKey(unb64(peer_pk)))
    return box.decrypt(unb64(ciphertext), unb64(nonce)).decode("utf-8")


def code4(device_pk: str, doctor_pk: str, token: str) -> str:
    digest = hashlib.sha256((device_pk + doctor_pk + token).encode("utf-8")).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 10000:04d}"
