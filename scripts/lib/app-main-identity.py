"""Compiler-free AppInstanceOwnership probe using macOS AppKit/Security.

Run only with an authenticated carrier interpreter, after source verification.
This mirrors the Main-role contract in AppInstanceOwnership.swift; it never
signals a bundle identifier, Frame host, worker, or an unproven process.
"""

from __future__ import annotations

import ctypes as c
import os
import re
import subprocess
import sys
from dataclasses import dataclass

IDENTIFIER = "io.vetcoders.vibecrafted"
TEAM = "MW223P3NPX"


@dataclass(frozen=True)
class Peer:
    pid: int
    bundle: str
    executable: str
    launch: float


class MacOS:
    def __init__(self) -> None:
        self.cf = c.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        self.security = c.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.appkit = c.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        self.objc = c.CDLL("/usr/lib/libobjc.A.dylib")
        self.objc.objc_getClass.argtypes = [c.c_char_p]
        self.objc.objc_getClass.restype = c.c_void_p
        self.objc.sel_registerName.argtypes = [c.c_char_p]
        self.objc.sel_registerName.restype = c.c_void_p
        self.cf.CFStringCreateWithCString.argtypes = [
            c.c_void_p,
            c.c_char_p,
            c.c_uint32,
        ]
        self.cf.CFStringCreateWithCString.restype = c.c_void_p
        self.cf.CFDictionaryGetValue.argtypes = [c.c_void_p, c.c_void_p]
        self.cf.CFDictionaryGetValue.restype = c.c_void_p
        self.cf.CFRelease.argtypes = [c.c_void_p]
        self.security.SecStaticCodeCreateWithPath.argtypes = [
            c.c_void_p,
            c.c_uint32,
            c.c_void_p,
        ]
        self.security.SecStaticCodeCreateWithPath.restype = c.c_int32
        self.security.SecStaticCodeCheckValidity.argtypes = [
            c.c_void_p,
            c.c_uint32,
            c.c_void_p,
        ]
        self.security.SecStaticCodeCheckValidity.restype = c.c_int32
        self.security.SecCodeCopySigningInformation.argtypes = [
            c.c_void_p,
            c.c_uint32,
            c.c_void_p,
        ]
        self.security.SecCodeCopySigningInformation.restype = c.c_int32
        pool_class = self.objc.objc_getClass(b"NSAutoreleasePool")
        self.pool = self.send(self.send(pool_class, "alloc"), "init")

    def send(self, target, selector, *args, result=c.c_void_p, types=()):
        function = c.CFUNCTYPE(result, c.c_void_p, c.c_void_p, *types)(
            ("objc_msgSend", self.objc)
        )
        return function(target, self.objc.sel_registerName(selector.encode()), *args)

    def string(self, value: str):
        return self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)

    def text(self, value) -> str:
        if not value:
            raise ValueError("missing string identity")
        raw = self.send(value, "UTF8String", result=c.c_char_p)
        if raw is None:
            raise ValueError("unreadable string identity")
        return raw.decode("utf-8")

    def path(self, url) -> str:
        if not url or not self.send(url, "isFileURL", result=c.c_bool):
            raise ValueError("missing file URL identity")
        host = self.send(url, "host")
        if (host and self.text(host) not in ("", "localhost")) or any(
            self.send(url, key) for key in ("query", "fragment")
        ):
            raise ValueError("foreign file URL identity")
        return os.path.normpath(self.text(self.send(url, "path")))

    def item(self, dictionary, name: str, *, exported=False):
        key = (
            c.c_void_p.in_dll(self.security, name).value
            if exported
            else self.string(name)
        )
        try:
            return self.cf.CFDictionaryGetValue(dictionary, key) if dictionary else None
        finally:
            if not exported:
                self.cf.CFRelease(key)

    def signed_main(self, bundle_url) -> str:
        code, info = c.c_void_p(), c.c_void_p()
        try:
            if self.security.SecStaticCodeCreateWithPath(bundle_url, 0, c.byref(code)):
                raise ValueError("missing static signature identity")
            # Full validation is stronger than Swift's basic metadata check.
            if self.security.SecStaticCodeCheckValidity(code, 0, None):
                raise ValueError("invalid destination signature")
            # TeamIdentifier is returned only with kSecCSSigningInformation.
            if self.security.SecCodeCopySigningInformation(code, 1 << 1, c.byref(info)):
                raise ValueError("missing signed metadata")
            if (
                self.text(self.item(info, "kSecCodeInfoIdentifier", exported=True))
                != IDENTIFIER
            ):
                raise ValueError("foreign signing identifier")
            if (
                self.text(self.item(info, "kSecCodeInfoTeamIdentifier", exported=True))
                != TEAM
            ):
                raise ValueError("foreign destination publisher")
            plist = self.item(info, "kSecCodeInfoPList", exported=True)
            if self.text(self.item(plist, "CFBundleIdentifier")) != IDENTIFIER:
                raise ValueError("foreign bundle identifier")
            name = self.text(self.item(plist, "CFBundleExecutable"))
            if name in ("", ".", "..") or "/" in name:
                raise ValueError("invalid signed Main name")
            main = self.path(
                self.item(info, "kSecCodeInfoMainExecutable", exported=True)
            )
            if main != self.path(bundle_url) + "/Contents/MacOS/" + name:
                raise ValueError("signed Main path disagrees")
            return main
        finally:
            for value in (info, code):
                if value:
                    self.cf.CFRelease(value)

    def peer(self, app, destination: str) -> Peer | None:
        if not app or self.send(app, "isTerminated", result=c.c_bool):
            return None
        bundle_url = self.send(app, "bundleURL")
        bundle = self.path(bundle_url)
        if bundle != destination:
            return None
        if self.text(self.send(app, "bundleIdentifier")) != IDENTIFIER:
            raise ValueError("running bundle identifier disagrees")
        pid = self.send(app, "processIdentifier", result=c.c_int32)
        if pid <= 0:
            raise ValueError("missing running pid")
        executable = self.path(self.send(app, "executableURL"))
        main = self.signed_main(bundle_url)
        if executable != main:
            if os.path.dirname(executable) != os.path.dirname(main):
                raise ValueError("foreign destination executable")
            return None  # proven non-Main host sharing the bundle identifier
        launch = self.send(app, "launchDate")
        if not launch:
            raise ValueError("missing Main launch date")
        return Peer(
            pid,
            bundle,
            executable,
            self.send(launch, "timeIntervalSince1970", result=c.c_double),
        )

    def snapshot(self, destination: str) -> list[Peer]:
        cls = self.objc.objc_getClass(b"NSRunningApplication")
        identifier = self.string(IDENTIFIER)
        try:
            apps = self.send(
                cls,
                "runningApplicationsWithBundleIdentifier:",
                identifier,
                types=(c.c_void_p,),
            )
        finally:
            self.cf.CFRelease(identifier)
        if not apps:
            raise ValueError("unavailable running-application inventory")
        peers = []
        for index in range(self.send(apps, "count", result=c.c_ulong)):
            app = self.send(apps, "objectAtIndex:", index, types=(c.c_ulong,))
            peer = self.peer(app, destination)
            if peer:
                peers.append(peer)
        if len(peers) > 1:
            raise ValueError("ambiguous destination Main instances")
        return peers

    def application(self, pid: int):
        return self.send(
            self.objc.objc_getClass(b"NSRunningApplication"),
            "runningApplicationWithProcessIdentifier:",
            pid,
            types=(c.c_int32,),
        )

    def terminate(self, app) -> bool:
        return self.send(app, "terminate", result=c.c_bool)


def process_start(pid: int) -> str:
    result = subprocess.run(
        ["/bin/ps", "-o", "lstart=", "-p", str(pid)],
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=True,
    )
    start = result.stdout.strip()
    if not re.fullmatch(
        r"[A-Z][a-z]{2} [A-Z][a-z]{2} +[0-9]{1,2} [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}",
        start,
    ):
        raise ValueError("malformed Main process start")
    return start


def inspect_main(api, destination: str, *, quit_main=False, start=process_start) -> str:
    peers = api.snapshot(destination)
    if not peers:
        if api.snapshot(destination):
            raise ValueError("destination Main appeared during identity check")
        return ""
    if len(peers) != 1:
        raise ValueError("ambiguous destination Main instances")
    selected = peers[0]
    identity = start(selected.pid)
    app = api.application(selected.pid)
    if (
        api.snapshot(destination) != [selected]
        or api.peer(app, destination) != selected
        or start(selected.pid) != identity
    ):
        raise ValueError("destination Main identity drifted before quit")
    if quit_main and not api.terminate(app):
        raise ValueError("Main refused graceful termination")
    return f"{selected.pid}\n{identity}"


if __name__ == "__main__":
    try:
        if len(sys.argv) not in (2, 3) or (
            len(sys.argv) == 3 and sys.argv[2] != "--quit"
        ):
            raise ValueError("expected destination [--quit]")
        result = inspect_main(
            MacOS(), os.path.abspath(sys.argv[1]), quit_main=len(sys.argv) == 3
        )
        if result:
            print(result)
    except Exception as error:
        print(f"install: refusing Main quit: {error}", file=sys.stderr)
        raise SystemExit(1) from error
