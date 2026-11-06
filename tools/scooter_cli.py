#!/usr/bin/env python3
"""Command-line tester that uses the integration's own protocol code (no Home Assistant).

    python tools/scooter_cli.py read  --mac AA:BB:.. --ltmk-file key.hex
    python tools/scooter_cli.py watch --mac AA:BB:.. --ltmk-file key.hex [--seconds 60]
    python tools/scooter_cli.py set   --mac AA:BB:.. --ltmk-file key.hex tail_light on

Requires: pip install bleak cryptography
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
import time
import types

PKG_DIR = os.path.join(os.path.dirname(__file__), "..", "custom_components", "mi_scooter_ble")


def _load(name: str):
    """Import protocol.py / client.py without executing the HA package __init__."""
    if "msb" not in sys.modules:
        pkg = types.ModuleType("msb")
        pkg.__path__ = [PKG_DIR]
        sys.modules["msb"] = pkg
    spec = importlib.util.spec_from_file_location(f"msb.{name}", os.path.join(PKG_DIR, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"msb.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


p = _load("protocol")
c = _load("client")

from bleak import BleakClient  # noqa: E402


def fmt(key, value):
    prop = p.PROPS[key]
    return p.scaled(prop, value)


async def open_session(mac: str, ltmk: bytes):
    client = BleakClient(mac, timeout=20.0)
    await client.connect()
    session = c.ScooterSession(client, ltmk)
    await session.start()
    return client, session


async def cmd_read(a, ltmk):
    client, s = await open_session(a.mac, ltmk)
    try:
        for key in p.LIVE_KEYS + p.SLOW_KEYS:
            print(f"{key:<22} {fmt(key, await s.get(p.PROPS[key]))}")
    finally:
        await client.disconnect()


async def cmd_watch(a, ltmk):
    client, s = await open_session(a.mac, ltmk)
    try:
        end = time.time() + a.seconds
        while time.time() < end:
            vals = {k: fmt(k, await s.get(p.PROPS[k])) for k in ("charging", "battery_level", *p.LIVENESS_KEYS)}
            print(time.strftime("%H:%M:%S"), vals, flush=True)
            await asyncio.sleep(2)
    finally:
        await client.disconnect()


async def cmd_set(a, ltmk):
    prop = p.PROPS[a.key]
    value = {"on": 1, "off": 0}.get(a.value.lower(), a.value)
    client, s = await open_session(a.mac, ltmk)
    try:
        print("before:", await s.get(prop))
        print("accepted:", await s.set(prop, int(value)))
        print("after:", await s.get(prop))
    finally:
        await client.disconnect()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["read", "watch", "set"])
    ap.add_argument("key", nargs="?")
    ap.add_argument("value", nargs="?")
    ap.add_argument("--mac", required=True)
    ap.add_argument("--ltmk-file", required=True)
    ap.add_argument("--seconds", type=float, default=60)
    a = ap.parse_args()
    ltmk = bytes.fromhex(open(a.ltmk_file, encoding="utf-8").read().strip())
    asyncio.run({"read": cmd_read, "watch": cmd_watch, "set": cmd_set}[a.command](a, ltmk))


if __name__ == "__main__":
    main()
