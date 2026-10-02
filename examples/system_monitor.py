#!/usr/bin/env python3
"""Live CPU sparkline + memory bar on the display, with a face that reacts to load.

    ld9900 dash &            # dashboard/API must be running
    python3 examples/system_monitor.py [--url http://host:8099] [--token T]
Linux only (reads /proc).
"""
import argparse
import collections
import time

from ld9900.client import LD


def cpu_times():
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:]]
    return sum(v), v[3] + v[4]          # total, idle+iowait


def mem_used():
    m = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            m[k] = int(v.split()[0])
    return 1 - m["MemAvailable"] / m["MemTotal"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--token")
    a = ap.parse_args()
    ld = LD(a.url, a.token)
    ld.mode("free")
    ld.face("neutral", x=0)
    hist = collections.deque([0.0] * 8, maxlen=8)
    last = cpu_times()
    mood = None
    while True:
        time.sleep(1)
        now = cpu_times()
        dt, di = now[0] - last[0], now[1] - last[1]
        last = now
        cpu = 1 - di / dt if dt else 0
        hist.append(cpu * 100)
        mem = mem_used()
        with ld.batch() as b:
            b.put(0, 8, "CPU")
            b.spark(0, 12, list(hist), lo=0, hi=100)
            b.put(1, 8, "MEM")
            b.bar(1, 12, mem, width=8)
        want = "dizzy" if cpu > 0.9 else "nervous" if cpu > 0.7 else "happy" if cpu < 0.2 else "neutral"
        if want != mood:
            ld.face(want)
            mood = want


if __name__ == "__main__":
    main()
