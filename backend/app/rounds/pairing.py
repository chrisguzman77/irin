"""R5: doctor / buddy pairing (POST /api/pair/start, the QR URL with
everything after #, code4 = first 4 bytes of sha256(device_pk || doctor_pk ||
token) mod 10000, confirm on the device with a FRESH PIN, revoke from either
side deletes keys on both). R5+: the OWNER pairing (DevicePairing): the kiosk
QR + 6-digit code shown only while hal.get_presence() is True, registered
with the relay, redeemed by the app; the token never travels in a URL query."""

from __future__ import annotations


def start_pairing(is_demo: bool):
    raise NotImplementedError("R5: pairing")
