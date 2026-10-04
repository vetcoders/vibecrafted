#!/usr/bin/env python3
"""Live vc-frame acceptance scenes over a real PTY client.

Canon (2026-10-04, dragon):
- populated world: navigation scenes run with >=2 sessions; a verdict of
  "dead" taken from a single-element world is not a verdict;
- every scene prints PASS/FAIL/WARN + one evidence line; exit != 0 on FAIL;
- isolated VC_FRAME_SOCKET_DIR: scenes never touch the user's sessions.

Scenes:
  S1 chrome        bare session renders topbar chips + rail + status bar
  S2 peer-switch   Super+Down/Up hops between two sessions (status bar name)
  S3 chords        Super+Right tab switch; Super+N floating switcher;
                   Super+Shift+. quick-cmd pane
  S4 close         x two-phase: first click arms (heavy glyph), second closes
  S5 organ-guard   Operator organ tabs draw no close glyph
                   (WARN unless --strict-organs: older generations predate
                   the guard)
"""

import argparse
import fcntl
import json
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time


def ensure_pyte():
    try:
        import pyte  # noqa

        return
    except ImportError:
        pass
    venv = os.path.join(os.environ.get("TMPDIR", "/tmp"), "vc-accept-venv")
    py = os.path.join(venv, "bin", "python3")
    if not os.path.exists(py):
        subprocess.run(
            [sys.executable, "-m", "venv", venv], check=True, capture_output=True
        )
        subprocess.run(
            [py, "-m", "pip", "install", "--quiet", "pyte"],
            check=True,
            capture_output=True,
        )
    os.execv(py, [py] + sys.argv)


ensure_pyte()
import pyte

COLS, ROWS = 200, 50
CLOSE = "✕"  # regular close mark
ARMED = "✖"  # armed heavy mark
ORGANS = ["Dashboard", "Active runs", "Config", "Doctor", "Projects"]


class Screen(pyte.Screen):
    def report_device_status(self, *a, **k):
        pass

    def report_device_attributes(self, *a, **k):
        pass


class ClientDied(RuntimeError):
    pass


class Client:
    def __init__(self, bin_, sock, session):
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "VC_FRAME",
                "VC_FRAME_PANE_ID",
                "VC_FRAME_SESSION_NAME",
                "ZELLIJ",
                "ZELLIJ_PANE_ID",
                "ZELLIJ_SESSION_NAME",
            )
        }
        env.update(
            VC_FRAME_SOCKET_DIR=sock, ZELLIJ_SOCKET_DIR=sock, TERM="xterm-256color"
        )
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        self.screen = Screen(COLS, ROWS)
        self.stream = pyte.Stream(self.screen)
        self.parse_errors: list[str] = []
        self.proc = subprocess.Popen(
            [bin_, "attach", session],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            start_new_session=True,
        )
        os.close(slave)

    def pump(self, sec):
        end = time.time() + sec
        while time.time() < end:
            r, _, _ = select.select([self.master], [], [], 0.2)
            if not r:
                continue
            try:
                d = os.read(self.master, 65536)
            except OSError:
                return
            if not d:
                continue
            if b"\x1b[6n" in d:
                os.write(self.master, b"\x1b[1;1R")
            try:
                self.stream.feed(d.decode("utf-8", "replace"))
            except Exception as exc:  # noqa: BLE001 — pyte rzuca rozmaite
                # błędy na egzotycznych sekwencjach; render-szum nie może
                # zabić sceny, ale zostaje w dzienniku klienta
                self.parse_errors.append(repr(exc))

    def rows(self):
        return [r.rstrip() for r in self.screen.display]

    def wait_for(self, pred, timeout=12):
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.5)
            if pred(self.rows()):
                return True
        return False

    def send(self, data):
        try:
            os.write(self.master, data)
        except OSError as e:
            raise ClientDied(str(e))

    def click(self, col, row):
        self.send(f"\x1b[<0;{col};{row}M".encode())
        time.sleep(0.12)
        self.send(f"\x1b[<0;{col};{row}m".encode())

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(3)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def run(bin_, sock, *args):
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in ("VC_FRAME", "VC_FRAME_SESSION_NAME", "ZELLIJ", "ZELLIJ_SESSION_NAME")
    }
    env.update(VC_FRAME_SOCKET_DIR=sock, ZELLIJ_SOCKET_DIR=sock)
    return subprocess.run(
        [bin_, *args], env=env, capture_output=True, text=True, timeout=30, check=False
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame-bin", default=None)
    ap.add_argument("--strict-organs", action="store_true")
    a = ap.parse_args()
    bin_ = a.frame_bin
    if not bin_:
        with open(os.path.expanduser("~/.local/share/vibecrafted/active.json")) as fh:
            active = json.load(fh)
        bin_ = os.path.join(active["runtime_root"], "libexec", "vc-frame")
    sock = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"vc-accept-{os.getpid()}")
    os.makedirs(sock, exist_ok=True)
    results = []

    def verdict(scene, ok, evidence, warn=False):
        tag = "WARN" if warn else ("PASS" if ok else "FAIL")
        print(f"[{tag}] {scene}: {evidence}")
        results.append((scene, tag))

    for s in ("accept-a", "accept-b"):
        run(bin_, sock, "attach", s, "--create-background")
    for _ in range(20):
        ls = run(bin_, sock, "list-sessions", "--no-formatting").stdout
        live = [l for l in ls.splitlines() if "EXITED" not in l]
        if sum(1 for l in live if "accept-" in l) >= 2:
            break
        time.sleep(0.5)

    c = Client(bin_, sock, "accept-a")
    try:
        # gotowość chrome'u nie zależy od glifu ✕ (starsze generacje go nie
        # rysują): rail + niepusty topbar i statusbar wystarczą
        up = c.wait_for(
            lambda r: (
                "SESSIONS" in "\n".join(r)
                and bool(r[0].strip())
                and bool(r[ROWS - 1].strip())
            )
        )
        top = c.rows()[0]
        status = c.rows()[ROWS - 1]
        verdict(
            "S1 chrome",
            up and bool(top.strip()) and bool(status.strip()),
            f"topbar={bool(top.strip())} rail={'SESSIONS' in chr(10).join(c.rows())} status={bool(status.strip())}",
        )

        # S2: populated-world peer switch; confirm via the status bar name,
        # tolerate one client re-exec during the native switch.
        def hop(cli, seq, want):
            try:
                cli.send(seq)
            except ClientDied:
                pass
            ok = False
            try:
                ok = cli.wait_for(lambda r: want in r[ROWS - 1], timeout=8)
            except ClientDied:
                pass
            if not ok and cli.proc.poll() is not None:
                cli.close()
                cli = Client(bin_, sock, want)
                ok = cli.wait_for(lambda r: want in r[ROWS - 1], timeout=8)
                return ok, cli, "client re-exec"
            return ok, cli, "in-place"

        ok_down, c, how_d = hop(c, b"\x1b[1;9B", "accept-b")
        ok_up, c, how_u = hop(c, b"\x1b[1;9A", "accept-a")
        verdict(
            "S2 peer-switch",
            ok_down and ok_up,
            f"down={ok_down}({how_d}) up={ok_up}({how_u})",
        )

        # S3a: tab switch
        before = c.rows()[0]
        c.send(b"\x1b[1;9C")
        c.pump(1.2)
        after = c.rows()[0]
        verdict("S3a Super+Right", before != after, "active chip moved")
        # S3b: floating switcher
        c.send(b"\x1b[110;9u")
        c.pump(1.8)
        has_sm = any("Session" in r and "Manager" in r for r in c.rows())
        verdict("S3b Super+N", has_sm, "Session Manager floating visible")
        c.send(b"\x1b")
        c.pump(1.0)
        # S3c: quick cmd
        c.send(b"\x1b[46;10u")
        c.pump(2.0)
        has_q = any("Quick cmd" in r for r in c.rows())
        verdict("S3c Super+Shift+.", has_q, "Quick cmd pane visible")
        c.send(b"\x1b")
        c.pump(0.8)

        # S4: two-phase close on a fresh user tab
        run(bin_, sock, "--session", "accept-a", "action", "new-tab", "--name", "scena")
        ok_tab = c.wait_for(lambda r: any("scena" in x and CLOSE in x for x in r[:2]))
        closed = armed = False
        if ok_tab:
            row = next(
                i for i, x in enumerate(c.rows()[:3]) if "scena" in x and CLOSE in x
            )
            line = c.rows()[row]
            col = line.find(CLOSE, line.find("scena"))
            c.click(col + 1, row + 1)
            c.pump(1.2)
            armed = ARMED in c.rows()[row]
            c.click(col + 1, row + 1)
            c.pump(1.5)
            layout = run(
                bin_, sock, "--session", "accept-a", "action", "dump-layout"
            ).stdout
            closed = "scena" not in layout
        verdict(
            "S4 close",
            ok_tab and armed and closed,
            f"glyph={ok_tab} armed={armed} closed={closed}",
        )

        # S5: organ guard
        toprow = c.rows()[0]
        leaky = [
            o for o in ORGANS if re.search(re.escape(o) + r"[^|]*" + CLOSE, toprow)
        ]
        verdict(
            "S5 organ-guard",
            not leaky,
            ("organs clean" if not leaky else f"close glyph on: {leaky}"),
            warn=bool(leaky) and not a.strict_organs,
        )
    finally:
        c.close()
        for s in ("accept-a", "accept-b"):
            run(bin_, sock, "kill-session", s)

    fails = [s for s, t in results if t == "FAIL"]
    print(f"== scenes: {len(results)} ran, fails: {len(fails)} ==")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
