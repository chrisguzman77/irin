"""R5: PyNaCl crypto_box (X25519 + XSalsa20-Poly1305, authenticated). The
device keypair is generated on first boot into backend/keys/ (gitignored);
seal(payload_json, peer_pk) -> {nonce, ciphertext}; open(...) authenticates
the sender (doctor messages). Patient data leaves the device only sealed to
the paired key (invariant 11)."""

from __future__ import annotations


def seal(payload_json: str, peer_pk: str) -> dict:
    raise NotImplementedError("R5: seal")


def open_box(nonce: str, ciphertext: str, peer_pk: str) -> str:
    raise NotImplementedError("R5: open")
