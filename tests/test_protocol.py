"""Unit tests for the HA-independent modules (protocol.py, cloud.py helpers)."""
import hashlib
import importlib.util
import os
import struct
import sys
import types

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PKG = os.path.join(os.path.dirname(__file__), "..", "custom_components", "mi_scooter_ble")


def load(name):
    if "msb" not in sys.modules:
        pkg = types.ModuleType("msb")
        pkg.__path__ = [PKG]
        sys.modules["msb"] = pkg
    spec = importlib.util.spec_from_file_location(f"msb.{name}", os.path.join(PKG, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"msb.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


p = load("protocol")
cloud = load("cloud")

# Real decrypted reply captured from a xiaomi.scooter.5max: GET battery_level -> 10 %.
REAL_REPLY = bytes.fromhex(
    "2d2001000306010200000001100a0000015df00400005ff00001055df00000005df00107000000049000802244"
)


def test_parse_real_reply():
    res = p.parse_reply(REAL_REPLY)
    assert (res.siid, res.piid, res.status, res.type_code) == (1, 2, 0, p.T_UINT8)
    assert p.decode_value(res.type_code, res.raw) == 10


def test_error_reply_is_five_bytes():
    plain = struct.pack("<HHBB", 0x200B, 1, 3, 1) + struct.pack("<BHH", 2, 4, 0xF05D)
    assert p.parse_reply(plain).status == 0xF05D


def test_get_and_set_frames():
    assert p.get_frame(1, 2, 7) == struct.pack("<HHBB", 0x200B, 7, 2, 1) + struct.pack("<BHH", 1, 2, 0)
    frame = p.set_frame(2, 4, p.T_BOOL, b"\x00", 1)
    assert frame == struct.pack("<HHBB", 0x200C, 1, 0, 1) + struct.pack("<BHH", 2, 4, 0x0001) + b"\x00"


def test_float_decoding_and_scale():
    raw = struct.pack("<f", 4587.0)
    assert p.scaled(p.PROPS["voltage"], p.decode_value(p.T_FLOAT, raw)) == 45.87
    assert p.scaled(p.PROPS["current"], p.decode_value(p.T_FLOAT, struct.pack("<f", -127.0))) == -1.27


def test_login_both_sides_agree():
    """Simulate the scooter side: same session key and a confirmation it can check."""
    ltmk = os.urandom(32)
    app_priv, app_pub = p.new_keypair()
    dev_priv = ec.generate_private_key(ec.SECP256R1())
    dev_pub = dev_priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)[1:]
    sk_app = p.session_key(app_priv, dev_pub, ltmk)
    sk_dev = p.session_key(dev_priv, app_pub, ltmk)
    assert sk_app == sk_dev and len(sk_app) == 64
    conf = p.login_confirmation(sk_app, dev_pub)
    plain = AESCCM(sk_dev[16:32], tag_length=4).decrypt(p.CONFIRM_NONCE, conf, None)
    import zlib
    assert plain == struct.pack("<I", zlib.crc32(dev_pub))


def test_spec_encryption_roundtrip_with_device_keys():
    sk = os.urandom(64)
    frame = p.get_frame(1, 2, 1)
    payload = p.encrypt_request(sk, 5, frame)
    # The scooter decrypts app->dev with key sk[16:32], iv sk[36:40].
    nonce = sk[36:40] + b"\0\0\0\0" + struct.pack("<I", 5)
    assert AESCCM(sk[16:32], tag_length=4).decrypt(nonce, payload[2:], None) == frame
    # And answers dev->app with key sk[0:16], iv sk[32:36].
    reply_nonce = sk[32:36] + b"\0\0\0\0" + struct.pack("<I", 9)
    ct = AESCCM(sk[0:16], tag_length=4).encrypt(reply_nonce, REAL_REPLY, None)
    assert p.decrypt_reply(sk, struct.pack("<H", 9) + ct) == REAL_REPLY


@pytest.mark.parametrize("data,kind", [
    (bytes.fromhex("0000040006f2"), "mng"),
    (bytes.fromhex("00000101"), "ack"),
    (bytes.fromhex("000000030400"), "ctr"),
    (bytes.fromhex("01006cd60d22"), "data"),
])
def test_packet_kinds_from_real_capture(data, kind):
    assert p.parse_packet(data)[0] == kind


def test_rc4_drop1024_known_vector():
    # RC4 test vector (key "Key", plaintext "Plaintext") after skipping 1024 bytes,
    # cross-checked against an independent implementation.
    key, data = b"Key", b"Plaintext"
    s, j = list(range(256)), 0
    for i in range(256):
        j = (j + s[i] + key[i % 3]) & 0xFF
        s[i], s[j] = s[j], s[i]
    i = j = 0
    stream = []
    for _ in range(1024 + len(data)):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        stream.append(s[(s[i] + s[j]) & 0xFF])
    expected = bytes(a ^ b for a, b in zip(data, stream[1024:]))
    assert cloud._rc4(key, data) == expected
    assert cloud._rc4(key, cloud._rc4(key, data)) == data


def test_pin_unwrap():
    ltmk, pin = os.urandom(32), "123456"
    enc = Cipher(algorithms.AES(hashlib.md5(pin.encode()).digest()), modes.CBC(cloud.LTMK_PIN_IV)).encryptor()
    wrapped = (enc.update(ltmk) + enc.finalize()).hex()
    assert cloud.decrypt_ltmk_with_pin(wrapped, pin) == ltmk
    assert cloud.decrypt_ltmk_with_pin(wrapped, "000000") != ltmk


def test_battery_extra():
    assert p.decode_battery_extra("0022aa00b800000403e8") == {"lifetime_energy": 8.874, "deep_discharges": 0}
