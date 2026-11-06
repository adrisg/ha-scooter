"""Xiaomi Mi Cloud: QR login, device list and BLE login key (LTMK) retrieval.

The request signing / RC4 scheme follows the public description used by
Xiaomi-cloud-tokens-extractor (MIT, (c) Piotr Machowski); this is an independent
asyncio implementation. Nothing here is logged in clear (tokens, keys).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import string
import time

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SERVERS = ("de", "i2", "ru", "sg", "us", "cn", "tw", "in")
LOGIN_URL = "https://account.xiaomi.com/longPolling/loginUrl"
# IV the Mi Home app uses to wrap the LTMK when the owner protected it with a PIN.
LTMK_PIN_IV = bytes.fromhex("7aa4c68c590d4031b980d98b41023800")


class CloudError(Exception):
    """Cloud request failed."""


class PinRequired(CloudError):
    """The key is protected with the Mi Home PIN."""


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    for n in range(1024 + len(data)):  # the first 1024 keystream bytes are dropped
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        if n >= 1024:
            out.append(data[n - 1024] ^ s[(s[i] + s[j]) & 0xFF])
    return bytes(out)


def decrypt_ltmk_with_pin(key_hex: str, pin: str) -> bytes:
    aes_key = hashlib.md5(pin.encode()).digest()
    dec = Cipher(algorithms.AES(aes_key), modes.CBC(LTMK_PIN_IV)).decryptor()
    return dec.update(bytes.fromhex(key_hex)) + dec.finalize()


def _json(text: str) -> dict:
    return json.loads(text.replace("&&&START&&&", ""))


class MiCloud:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._http = session
        self._agent = (
            "".join(random.choices(string.ascii_lowercase, k=18))
            + "-"
            + "".join(random.choices("ABCDE", k=13))
            + " APP/com.xiaomi.mihome APPV/10.5.201"
        )
        self.user_id: str | None = None
        self._ssecurity: str | None = None
        self._service_token: str | None = None
        self._lp_url: str | None = None
        self._lp_timeout = 300
        self.qr_image_url: str | None = None
        self.login_url: str | None = None

    # ------------------------------------------------------------- QR login
    async def start_qr_login(self) -> None:
        params = {
            "_qrsize": "480", "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "callback": "https://sts.api.io.mi.com/sts", "_hasLogo": "false",
            "sid": "xiaomiio", "serviceParam": "", "_locale": "en_GB",
            "_dc": str(int(time.time() * 1000)),
        }
        async with self._http.get(LOGIN_URL, params=params) as resp:
            if resp.status != 200:
                raise CloudError(f"loginUrl HTTP {resp.status}")
            data = _json(await resp.text())
        self.qr_image_url, self.login_url = data["qr"], data["loginUrl"]
        self._lp_url, self._lp_timeout = data["lp"], int(data.get("timeout", 300))

    async def wait_qr_login(self) -> None:
        """Long-poll until the QR is scanned and confirmed in Mi Home."""
        deadline = time.monotonic() + self._lp_timeout
        data = None
        while time.monotonic() < deadline:
            try:
                async with self._http.get(self._lp_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = _json(await resp.text())
                        break
            except asyncio.TimeoutError:
                continue
        if not data:
            raise CloudError("QR login timed out")
        self.user_id = str(data["userId"])
        self._ssecurity = data["ssecurity"]
        async with self._http.get(data["location"]) as resp:
            if resp.status != 200:
                raise CloudError(f"service token HTTP {resp.status}")
        token = resp.cookies.get("serviceToken")
        if token is None:
            for cookie in self._http.cookie_jar:
                if cookie.key == "serviceToken":
                    token = cookie
        if token is None:
            raise CloudError("no service token")
        self._service_token = token.value

    # ---------------------------------------------------------- signed API
    def _signed_nonce(self, nonce: str) -> str:
        digest = hashlib.sha256(base64.b64decode(self._ssecurity) + base64.b64decode(nonce)).digest()
        return base64.b64encode(digest).decode()

    @staticmethod
    def _signature(method: str, url: str, params: dict, signed_nonce: str) -> str:
        parts = [method, url.split("com", 1)[1].replace("/app/", "/")]
        parts += [f"{k}={v}" for k, v in params.items()] + [signed_nonce]
        return base64.b64encode(hashlib.sha1("&".join(parts).encode()).digest()).decode()

    async def _call(self, country: str, path: str, data: dict) -> dict:
        if not self._service_token:
            raise CloudError("not logged in")
        host = "api.io.mi.com" if country == "cn" else f"{country}.api.io.mi.com"
        url = f"https://{host}/app{path}"
        millis = int(time.time() * 1000)
        nonce = base64.b64encode(os.urandom(8) + (millis // 60000).to_bytes(4, "big")).decode()
        sn = self._signed_nonce(nonce)
        key = base64.b64decode(sn)
        params = {"data": json.dumps(data, separators=(",", ":"))}
        params["rc4_hash__"] = self._signature("POST", url, params, sn)
        params = {k: base64.b64encode(_rc4(key, v.encode())).decode() for k, v in params.items()}
        params.update(signature=self._signature("POST", url, params, sn),
                      ssecurity=self._ssecurity, _nonce=nonce)
        headers = {
            "Accept-Encoding": "identity", "User-Agent": self._agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2", "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
            "Cookie": "; ".join(f"{k}={v}" for k, v in {
                "userId": self.user_id, "yetAnotherServiceToken": self._service_token,
                "serviceToken": self._service_token, "locale": "en_GB",
                "channel": "MI_APP_STORE"}.items()),
        }
        async with self._http.post(url, params=params, headers=headers) as resp:
            if resp.status != 200:
                raise CloudError(f"{path} HTTP {resp.status}")
            text = await resp.text()
        return json.loads(_rc4(base64.b64decode(self._signed_nonce(nonce)), base64.b64decode(text)))

    # ------------------------------------------------------------- devices
    async def list_scooters(self) -> list[dict]:
        """All BLE scooters on the account: {did, name, model, mac, country}."""
        found: list[dict] = []
        for country in SERVERS:
            try:
                homes = await self._call(country, "/v2/homeroom/gethome", {
                    "fg": True, "fetch_share": True, "fetch_share_dev": True, "limit": 300, "app_ver": 7})
            except (CloudError, aiohttp.ClientError, ValueError):
                continue
            for home in (homes.get("result") or {}).get("homelist") or []:
                devs = await self._call(country, "/v2/home/home_device_list", {
                    "home_owner": int(home.get("uid") or self.user_id), "home_id": int(home["id"]),
                    "limit": 200, "get_split_device": True, "support_smart_home": True})
                for d in (devs.get("result") or {}).get("device_info") or []:
                    if "scooter" in (d.get("model") or "") and d.get("did"):
                        found.append({"did": d["did"], "name": d.get("name") or d["model"],
                                      "model": d["model"], "mac": (d.get("mac") or "").upper(),
                                      "country": country})
            if found:
                break
        return found

    async def fetch_ltmk(self, did: str, country: str, pin: str | None = None) -> bytes:
        resp = await self._call(country, "/share/askbluetoothkey", {"type": "own", "did": str(did), "keyid": 0})
        result = resp.get("result") or {}
        key_hex = result.get("key")
        if resp.get("code") not in (0, None) or not key_hex:
            raise CloudError(f"askbluetoothkey failed (code={resp.get('code')})")
        if result.get("encrypt_type", 0) == 0:
            ltmk = bytes.fromhex(key_hex)
        elif not pin:
            raise PinRequired
        else:
            ltmk = decrypt_ltmk_with_pin(key_hex, pin)
        if len(ltmk) != 32:
            raise CloudError("unexpected key length")
        return ltmk
