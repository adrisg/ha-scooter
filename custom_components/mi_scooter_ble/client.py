"""BLE session with the scooter on top of an already connected BleakClient.

Independent from Home Assistant so it can also be used from a plain script
(see tools/scooter_cli.py). Only one request is in flight at a time.
"""
from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient

from . import protocol as p

_LOGGER = logging.getLogger(__name__)

TIMEOUT = 8.0


class ScooterError(Exception):
    """Generic protocol error."""


class LoginRejected(ScooterError):
    """The scooter refused the key (wrong LTMK / PIN)."""


class ScooterSession:
    def __init__(self, client: BleakClient, ltmk: bytes) -> None:
        self._client = client
        self._ltmk = ltmk
        self._chars: dict[int, object] = {}
        self._rx: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
        self._pending: list[tuple[int, str, dict]] = []
        self._login_result: asyncio.Future | None = None
        self._sk: bytes | None = None
        self._counter = 0
        self._tid = 0
        self._dmtu = 242
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ setup
    async def start(self) -> None:
        service = next(
            (s for s in self._client.services if str(s.uuid).lower() == p.SERVICE_UUID), None
        )
        if service is None:
            raise ScooterError("FE95 service not found")
        self._chars = {p.short_id(c.uuid): c for c in service.characteristics}
        for cid in (p.CHAR_CONTROL, p.CHAR_LOGIN, p.CHAR_SPEC_TX, p.CHAR_SPEC_RX):
            if cid not in self._chars:
                raise ScooterError(f"characteristic 0x{cid:04x} missing")
        for cid, char in self._chars.items():
            if {"notify", "indicate"} & {str(x).lower() for x in char.properties}:
                await self._client.start_notify(char, self._make_handler(cid))
        await self._login()

    def _make_handler(self, cid: int):
        def handler(_sender, data: bytearray) -> None:
            data = bytes(data)
            if cid == p.CHAR_CONTROL and data[:1] in (bytes([p.LOGIN_OK]), bytes([p.LOGIN_FAIL])):
                if self._login_result and not self._login_result.done():
                    self._login_result.set_result(data[0] == p.LOGIN_OK)
                return
            kind, info = p.parse_packet(data)
            if kind == "mng":  # the scooter may (re)announce its limits at any time
                self._dmtu = info["max_dmtu"] or self._dmtu
                asyncio.ensure_future(
                    self._write(cid, p.mng_ack(info["subtype"], info["max_pkg"], info["max_dmtu"]))
                )
                return
            self._rx.put_nowait((cid, data))

        return handler

    async def _write(self, cid: int, data: bytes) -> None:
        await self._client.write_gatt_char(self._chars[cid], data, response=False)

    def _flush(self) -> None:
        self._pending.clear()
        while not self._rx.empty():
            self._rx.get_nowait()

    async def _next(self, deadline: float, chars: tuple[int, ...]) -> tuple[int, str, dict]:
        loop = asyncio.get_running_loop()
        for i, item in enumerate(self._pending):
            if item[0] in chars:
                del self._pending[i]
                return item
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ScooterError("timeout waiting for the scooter")
            try:
                cid, data = await asyncio.wait_for(self._rx.get(), remaining)
            except asyncio.TimeoutError as err:
                raise ScooterError("timeout waiting for the scooter") from err
            if cid in chars:
                kind, info = p.parse_packet(data)
                return cid, kind, info

    # --------------------------------------------------------- framed channels
    async def _send(self, cid: int, channel: int, payload: bytes, size: int,
                    ack_chars: tuple[int, ...]) -> None:
        """Send a framed message and run the ack / resend loop until 'done'."""
        loop = asyncio.get_running_loop()
        frames = p.split(payload, size)
        self._flush()
        await self._write(cid, p.ctr(channel, len(frames)))
        deadline = loop.time() + TIMEOUT
        sent = False
        while True:
            got_cid, kind, info = await self._next(deadline, ack_chars)
            if kind != "ack":
                # The reply may start before the final ack: keep it for _receive.
                self._pending.append((got_cid, kind, info))
                if kind in ("ctr", "single") and sent:
                    return
                continue
            status = info["status"]
            if status == p.ACK_READY and not sent:
                sent = True
                for i, chunk in enumerate(frames, 1):
                    await self._write(cid, p.data_frame(i, chunk))
                    await asyncio.sleep(0.02)
            elif status == p.ACK_RESEND:
                for seq in info["seqs"]:
                    if 1 <= seq <= len(frames):
                        await self._write(cid, p.data_frame(seq, frames[seq - 1]))
                        await asyncio.sleep(0.02)
            elif status == p.ACK_DONE and sent:
                return
            elif not sent and status is not None:
                # Some channels skip the explicit "ready": send right away.
                sent = True
                for i, chunk in enumerate(frames, 1):
                    await self._write(cid, p.data_frame(i, chunk))
                    await asyncio.sleep(0.02)

    async def _receive(self, cid: int, deadline: float) -> bytes:
        """Receive one framed message (CTR + data fragments, or SINGLE)."""
        total = None
        parts: dict[int, bytes] = {}
        while True:
            _, kind, info = await self._next(deadline, (cid,))
            if kind == "single":
                await self._write(cid, p.single_ack())
                return info["payload"]
            if kind == "ctr":
                total, parts = info["frames"], {}
                await self._write(cid, p.ack(p.ACK_READY))
            elif kind == "data" and total:
                parts[info["seq"]] = info["payload"]
                if len(parts) >= total:
                    await self._write(cid, p.ack(p.ACK_DONE))
                    return b"".join(parts[i] for i in sorted(parts))

    # ------------------------------------------------------------------ login
    async def _login(self) -> None:
        loop = asyncio.get_running_loop()
        self._flush()
        await self._write(p.CHAR_CONTROL, p.CMD_HANDSHAKE)
        await asyncio.sleep(0.8)  # MNG exchange is answered by the notify handler

        priv, pub = p.new_keypair()
        self._flush()
        await self._write(p.CHAR_CONTROL, p.CMD_LOGIN_START)
        await asyncio.sleep(0.2)
        await self._send(p.CHAR_LOGIN, p.LOGIN_CH_PUBKEY, pub, self._dmtu - 2, (p.CHAR_LOGIN,))
        device_pub = await self._receive(p.CHAR_LOGIN, loop.time() + TIMEOUT)
        if len(device_pub) < 64:
            raise ScooterError("bad device public key")

        sk = p.session_key(priv, device_pub, self._ltmk)
        self._login_result = loop.create_future()
        await self._send(
            p.CHAR_LOGIN, p.LOGIN_CH_CONFIRM, p.login_confirmation(sk, device_pub),
            self._dmtu - 2, (p.CHAR_LOGIN,),
        )
        try:
            ok = await asyncio.wait_for(self._login_result, TIMEOUT)
        except asyncio.TimeoutError as err:
            raise ScooterError("no login result") from err
        if not ok:
            raise LoginRejected("scooter rejected the key")
        self._sk = sk
        self._counter = 0
        _LOGGER.debug("login ok")

    # -------------------------------------------------------------- spec calls
    async def _request(self, frame: bytes) -> p.SpecResult:
        if self._sk is None:
            raise ScooterError("not logged in")
        async with self._lock:
            loop = asyncio.get_running_loop()
            payload = p.encrypt_request(self._sk, self._counter, frame)
            self._counter += 1
            await self._send(
                p.CHAR_SPEC_TX, p.SPEC_CHANNEL, payload, p.SPEC_FRAME_SIZE,
                (p.CHAR_SPEC_TX, p.CHAR_SPEC_RX),
            )
            reply = await self._receive(p.CHAR_SPEC_RX, loop.time() + TIMEOUT)
            return p.parse_reply(p.decrypt_reply(self._sk, reply))

    def _next_tid(self) -> int:
        self._tid = (self._tid % 0xFFFF) + 1
        return self._tid

    async def get(self, prop: p.Prop):
        """Read one property; returns the decoded raw value (unscaled) or None."""
        res = await self._request(p.get_frame(prop.siid, prop.piid, self._next_tid()))
        if res.status:
            return None
        return p.decode_value(res.type_code, res.raw)

    async def set(self, prop: p.Prop, value) -> bool:
        if prop.writable_type is None:
            raise ScooterError(f"{prop.key} is read-only")
        frame = p.set_frame(prop.siid, prop.piid, prop.writable_type,
                            p.encode_value(prop.writable_type, value), self._next_tid())
        res = await self._request(frame)
        return res.status == 0
