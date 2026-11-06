"""Pure protocol helpers for Xiaomi/Dreame scooters over BLE (no I/O, no Home Assistant).

Covers:
  * Mijia "security chip" login crypto (ECDH P-256 + HKDF-SHA256 + AES-CCM).
  * MIoT-spec frames (GET / SET) carried over the FE95 service, and their encryption.
  * Value decoding and the property catalogue with real-world scales.

Everything here was learned by observation on a xiaomi.scooter.5max; see docs/PROTOCOL.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
import zlib

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

# --------------------------------------------------------------------------- GATT
SERVICE_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"

CHAR_CONTROL = 0x0010  # 0xA4 handshake, login start, login result (0x21 ok / 0x22 fail)
CHAR_LOGIN = 0x0016  # framed channel used during login
CHAR_SPEC_TX = 0x001A  # framed channel: app -> scooter MIoT-spec requests
CHAR_SPEC_RX = 0x001B  # framed channel: scooter -> app MIoT-spec replies


def short_id(uuid: str) -> int:
    """16-bit id of a characteristic UUID (0000xxxx-0000-1000-8000-00805f9b34fb)."""
    return int(str(uuid).replace("-", "")[:8], 16) & 0xFFFF


# --------------------------------------------------------------- framed transport
# Every framed message starts with a little-endian u16 sequence number.
# seq == 0 -> control packet [0000][type][...]; seq >= 1 -> data fragment [seq][payload].
PKT_CTR = 0x00  # [0000][00][channel][frame_count u16]
PKT_ACK = 0x01  # [0000][01][status][seq u16 ...]   status 01 ready, 00 done, 05 resend
PKT_SINGLE = 0x02  # [0000][02][channel][data]
PKT_SINGLE_ACK = 0x03
PKT_MNG = 0x04  # [0000][04][subtype][max_pkg][max_dmtu]
PKT_MNG_ACK = 0x05

ACK_DONE = 0x00
ACK_READY = 0x01
ACK_RESEND = 0x05

CMD_HANDSHAKE = bytes([0xA4])
CMD_LOGIN_START = bytes([0x20, 0x00, 0x00])
LOGIN_OK = 0x21
LOGIN_FAIL = 0x22

LOGIN_CH_PUBKEY = 3
LOGIN_CH_CONFIRM = 5
SPEC_CHANNEL = 0
SPEC_FRAME_SIZE = 18  # payload bytes per data fragment on the spec channel


def ctr(channel: int, frame_count: int) -> bytes:
    return struct.pack("<HBBH", 0, PKT_CTR, channel, frame_count)


def ack(status: int) -> bytes:
    return struct.pack("<HBB", 0, PKT_ACK, status)


def mng_ack(subtype: int, max_pkg: int, max_dmtu: int) -> bytes:
    return struct.pack("<HBB", 0, PKT_MNG_ACK, subtype) + bytes([max_pkg, max_dmtu])


def single_ack() -> bytes:
    return struct.pack("<HBB", 0, PKT_SINGLE_ACK, 0)


def data_frame(seq: int, chunk: bytes) -> bytes:
    return struct.pack("<H", seq) + chunk


def split(payload: bytes, size: int) -> list[bytes]:
    return [payload[i : i + size] for i in range(0, len(payload), size)] or [b""]


def parse_packet(data: bytes) -> tuple[str, dict]:
    """Classify a notification from a framed characteristic."""
    if len(data) < 3:
        return "short", {}
    seq = struct.unpack_from("<H", data)[0]
    if seq:
        return "data", {"seq": seq, "payload": data[2:]}
    kind = data[2]
    if kind == PKT_ACK:
        status = data[3] if len(data) > 3 else None
        seqs = [struct.unpack_from("<H", data, i)[0] for i in range(4, len(data) - 1, 2)]
        return "ack", {"status": status, "seqs": seqs}
    if kind == PKT_CTR and len(data) >= 6:
        return "ctr", {"channel": data[3], "frames": struct.unpack_from("<H", data, 4)[0]}
    if kind == PKT_SINGLE:
        return "single", {"channel": data[3] if len(data) > 3 else 0, "payload": data[4:]}
    if kind == PKT_MNG and len(data) >= 6:
        return "mng", {"subtype": data[3], "max_pkg": data[4], "max_dmtu": data[5]}
    return "other", {"type": kind}


# ------------------------------------------------------------------ login crypto
LOGIN_SALT = b"smartcfg-login-salt"
LOGIN_INFO = b"smartcfg-login-info"
CONFIRM_NONCE = bytes(range(0x10, 0x1C))


def new_keypair() -> tuple[ec.EllipticCurvePrivateKey, bytes]:
    """P-256 key pair; public key as raw 64 bytes X||Y."""
    priv = ec.generate_private_key(ec.SECP256R1())
    raw = priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return priv, raw[1:]


def session_key(priv: ec.EllipticCurvePrivateKey, device_pub: bytes, ltmk: bytes) -> bytes:
    """64-byte session key = HKDF-SHA256(ECDH_shared || LTMK)."""
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + device_pub[:64])
    shared = priv.exchange(ec.ECDH(), peer)
    return HKDF(algorithm=hashes.SHA256(), length=64, salt=LOGIN_SALT, info=LOGIN_INFO).derive(
        shared + ltmk
    )


def login_confirmation(sk: bytes, device_pub: bytes) -> bytes:
    """App proof of the shared key: AES-CCM(CRC32(device_pub) LE) with sk[16:32]."""
    plain = struct.pack("<I", zlib.crc32(device_pub[:64]) & 0xFFFFFFFF)
    return AESCCM(sk[16:32], tag_length=4).encrypt(CONFIRM_NONCE, plain, None)


# --------------------------------------------------------------- spec encryption
def _nonce(iv: bytes, counter: int) -> bytes:
    return iv + b"\x00\x00\x00\x00" + struct.pack("<I", counter & 0xFFFFFFFF)


def encrypt_request(sk: bytes, counter: int, frame: bytes) -> bytes:
    """app -> scooter: [counter u16][AES-CCM(key=sk[16:32], iv=sk[36:40])]."""
    ct = AESCCM(sk[16:32], tag_length=4).encrypt(_nonce(sk[36:40], counter), frame, None)
    return struct.pack("<H", counter & 0xFFFF) + ct


def decrypt_reply(sk: bytes, payload: bytes) -> bytes:
    """scooter -> app: [counter u16][AES-CCM(key=sk[0:16], iv=sk[32:36])]."""
    counter = struct.unpack_from("<H", payload)[0]
    return AESCCM(sk[0:16], tag_length=4).decrypt(_nonce(sk[32:36], counter), payload[2:], None)


# -------------------------------------------------------------------- spec frames
OP_SET = 0
OP_GET = 2

T_BOOL, T_UINT8, T_INT8, T_UINT16, T_INT16 = 0, 1, 2, 3, 4
T_UINT32, T_INT32, T_UINT64, T_INT64, T_FLOAT, T_STRING = 5, 6, 7, 8, 9, 10


def _frame(op: int, tid: int, body: bytes) -> bytes:
    total = 6 + len(body)
    return struct.pack("<HHBB", (total | 0x2000) & 0xFFFF, tid & 0xFFFF, op, 1) + body


def get_frame(siid: int, piid: int, tid: int) -> bytes:
    """Read one property. The scooter only answers the first object of a batch."""
    return _frame(OP_GET, tid, struct.pack("<BHH", siid, piid, 0))


def set_frame(siid: int, piid: int, type_code: int, value: bytes, tid: int) -> bytes:
    tl = ((type_code & 0xF) << 12) | (len(value) & 0xFFF)
    return _frame(OP_SET, tid, struct.pack("<BHH", siid, piid, tl) + value)


@dataclass
class SpecResult:
    siid: int
    piid: int
    status: int
    type_code: int | None = None
    raw: bytes = b""


def parse_reply(plain: bytes) -> SpecResult:
    """Parse the first object of a decrypted reply.

    GET ok:  [siid][piid u16][status u16][(type<<12)|len u16][value]
    error / SET reply: [siid][piid u16][status u16]
    """
    if len(plain) < 11:
        raise ValueError(f"reply too short: {plain.hex()}")
    siid, piid, status = struct.unpack_from("<BHH", plain, 6)
    if status or len(plain) < 13:
        return SpecResult(siid, piid, status)
    tl = struct.unpack_from("<H", plain, 11)[0]
    return SpecResult(siid, piid, 0, tl >> 12, plain[13 : 13 + (tl & 0xFFF)])


def decode_value(type_code: int | None, raw: bytes):
    if not raw or type_code is None:
        return None
    if type_code == T_FLOAT and len(raw) == 4:
        return struct.unpack("<f", raw)[0]
    if type_code == T_STRING:
        return raw.decode("utf-8", "replace")
    signed = type_code in (T_INT8, T_INT16, T_INT32, T_INT64)
    value = int.from_bytes(raw, "little", signed=signed)
    return bool(value) if type_code == T_BOOL else value


def encode_value(type_code: int, value) -> bytes:
    if type_code == T_BOOL:
        return bytes([1 if value else 0])
    if type_code == T_UINT8:
        return bytes([int(value) & 0xFF])
    raise ValueError(f"unsupported type for writing: {type_code}")


# --------------------------------------------------------------- property catalogue
@dataclass(frozen=True)
class Prop:
    siid: int
    piid: int
    key: str
    scale: float = 1.0  # real value = raw * scale
    writable_type: int | None = None  # T_BOOL / T_UINT8 when it can be SET


PROPS: dict[str, Prop] = {
    p.key: p
    for p in (
        # siid 1 — battery / ride
        Prop(1, 1, "riding_mode"),
        Prop(1, 2, "battery_level"),
        Prop(1, 3, "remaining_capacity"),  # mAh
        Prop(1, 4, "voltage", 0.01),  # V
        Prop(1, 5, "current", 0.01),  # A, negative while charging (5 Max)
        Prop(1, 6, "power", 0.01),  # W (5 Max)
        Prop(1, 7, "remaining_range", 0.01),  # km
        Prop(1, 8, "fault"),
        Prop(1, 9, "trip_distance", 0.01),  # km
        # siid 2 — ride state & settings
        Prop(2, 1, "average_speed", 0.01),  # km/h
        Prop(2, 2, "locked"),
        Prop(2, 3, "cruise_control", writable_type=T_BOOL),
        Prop(2, 4, "tail_light", writable_type=T_BOOL),
        Prop(2, 5, "energy_recovery"),
        Prop(2, 6, "total_distance", 0.01),  # km
        Prop(2, 7, "riding"),
        Prop(2, 8, "riding_time"),  # s
        Prop(2, 9, "highest_speed", 0.01),  # km/h
        Prop(2, 10, "asr"),
        Prop(2, 12, "auto_light", writable_type=T_BOOL),
        Prop(2, 13, "tcs", writable_type=T_BOOL),
        Prop(2, 14, "intelligent_downhill", writable_type=T_BOOL),
        Prop(2, 15, "hill_parking", writable_type=T_BOOL),
        Prop(2, 16, "ambient_light", writable_type=T_UINT8),  # 0 off, 1 on, 2 active
        # siid 3 — battery health & misc
        Prop(3, 1, "battery_status"),
        Prop(3, 2, "battery_temperature"),  # °C
        Prop(3, 3, "scooter_temperature"),  # °C
        Prop(3, 10, "charging"),
        Prop(3, 11, "charge_cycles"),
        Prop(3, 12, "battery_health"),  # SOH %
        # siid 4 — identification
        Prop(4, 3, "bms_firmware"),
        Prop(4, 5, "firmware"),
        Prop(4, 7, "battery_extra"),  # hex string, see decode_battery_extra
    )
}

# Read on every refresh while the scooter is powered on.
LIVE_KEYS = (
    "battery_level", "voltage", "current", "power", "charging", "remaining_capacity",
    "remaining_range", "riding", "trip_distance", "average_speed", "highest_speed",
    "riding_time", "battery_temperature", "scooter_temperature", "fault", "riding_mode",
)
# Read less often (settings, counters, identification).
SLOW_KEYS = (
    "total_distance", "charge_cycles", "battery_health", "battery_status", "locked",
    "cruise_control", "tail_light", "auto_light", "ambient_light", "tcs",
    "intelligent_downhill", "hill_parking", "energy_recovery", "asr",
    "firmware", "bms_firmware", "battery_extra",
)
# Fast-changing values used to tell "powered on" (live) from "off" (frozen cache).
LIVENESS_KEYS = ("current", "power", "voltage")


def scaled(prop: Prop, value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and prop.scale != 1.0:
        return round(value * prop.scale, 2)
    return value


def decode_battery_extra(value: str | None) -> dict:
    """siid 4 piid 7: hex string -> lifetime energy (kWh) and deep discharges."""
    try:
        s = (value or "").strip().lower()
        return {"lifetime_energy": int(s[0:6], 16) / 1000.0, "deep_discharges": int(s[10:14], 16)}
    except ValueError:
        return {}
