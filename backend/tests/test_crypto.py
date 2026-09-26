"""R5 check: seal/open round trip; a tampered ciphertext is rejected; a box
from any other key is rejected; the device keypair is generated once, 0600,
in the gitignored keys dir; code4 is deterministic and 4 digits."""

import os
import re
import stat

import pytest
from nacl.public import PrivateKey

from app.rounds import crypto
from app.rounds.crypto import CryptoError, code4, generate_keypair, open_box, seal, unb64


def test_round_trip_and_authentication(tmp_path):
    device = crypto.device_keypair(tmp_path)
    doc_sk_b64, doc_pk_b64 = generate_keypair()
    box = seal('{"kind": "basal_check", "n": 8}', doc_pk_b64, private_key=device)
    assert set(box) == {"nonce", "ciphertext"}
    # the doctor's browser opens it with the device's public key
    device_pk = crypto.b64(bytes(device.public_key))
    assert open_box(box["nonce"], box["ciphertext"], device_pk, private_key=PrivateKey(unb64(doc_sk_b64))) == '{"kind": "basal_check", "n": 8}'
    # and the device opens a doctor message sealed to it
    reply = seal("hold_step", device_pk, private_key=PrivateKey(unb64(doc_sk_b64)))
    assert open_box(reply["nonce"], reply["ciphertext"], doc_pk_b64, private_key=device) == "hold_step"


def test_tampered_or_wrong_sender_is_rejected(tmp_path):
    device = crypto.device_keypair(tmp_path)
    doc_sk, doc_pk = generate_keypair()
    box = seal("plan", doc_pk, private_key=device)
    raw = bytearray(unb64(box["ciphertext"]))
    raw[-1] ^= 0x01
    with pytest.raises(CryptoError):
        open_box(box["nonce"], crypto.b64(bytes(raw)), crypto.b64(bytes(device.public_key)), private_key=PrivateKey(unb64(doc_sk)))
    other_sk, other_pk = generate_keypair()  # a box sealed by an unpaired key never opens as the doctor's
    forged = seal("plan", crypto.b64(bytes(device.public_key)), private_key=PrivateKey(unb64(other_sk)))
    with pytest.raises(CryptoError):
        open_box(forged["nonce"], forged["ciphertext"], doc_pk, private_key=device)


def test_device_keypair_is_generated_once_and_private(tmp_path):
    a = crypto.device_keypair(tmp_path)
    b = crypto.device_keypair(tmp_path)
    assert bytes(a) == bytes(b) and (tmp_path / "device_public.key").read_text().strip() == crypto.device_public_key(tmp_path)
    mode = stat.S_IMODE(os.stat(tmp_path / "device_private.key").st_mode)
    assert mode == 0o600


def test_code4_is_four_digits_and_binds_both_keys_and_the_token():
    a = code4("devpk", "docpk", "ab" * 16)
    assert re.fullmatch(r"\d{4}", a) and a == code4("devpk", "docpk", "ab" * 16)
    assert a != code4("devpk", "otherpk", "ab" * 16) or a != code4("devpk", "docpk", "cd" * 16)
    assert code4("A", "B", "C") == f"{int.from_bytes(__import__('hashlib').sha256(b'ABC').digest()[:4], 'big') % 10000:04d}"
