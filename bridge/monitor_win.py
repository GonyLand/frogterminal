"""CPU 팝업(윈도우판) — 맥판의 htop 자리. 2초마다 CPU·메모리 상위 프로세스를 그린다. q 로 끝낸다.

세션 안에서 도는 작은 터미널 프로그램이다(bridge/sessions.win_launch 가 띄운다). 에이전트 여럿이
동시에 돌 때 PC 가 버티는지 바로 보려는 용도라 화면 하나에 숫자만 둔다.
"""
import msvcrt
import os
import time

import psutil

TOP = 18


def sample() -> list[tuple[float, int, str, int]]:
    rows = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
        info = p.info
        mem = info["memory_info"].rss if info.get("memory_info") else 0
        rows.append((info.get("cpu_percent") or 0.0, info["pid"], (info.get("name") or "?")[:28], mem))
    rows.sort(key=lambda r: (r[0], r[3]), reverse=True)
    return rows[:TOP]


def draw() -> str:
    vm = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    lines = [f"\x1b[1mCPU {cpu:5.1f}%   RAM {vm.used / 2**30:5.1f}/{vm.total / 2**30:.1f} GB ({vm.percent:.0f}%)"
             f"   프로세스 {len(psutil.pids())}개\x1b[0m   (q = 닫기)",
             "",
             f"\x1b[7m{'PID':>7} {'CPU%':>6} {'MB':>7}  이름\x1b[0m"]
    for cpu_p, pid, name, mem in sample():
        lines.append(f"{pid:>7} {cpu_p:>6.1f} {mem / 2**20:>7.0f}  {name}")
    return "\x1b[H\x1b[2J" + "\r\n".join(lines)


def main() -> None:
    psutil.cpu_percent(interval=None)                    # 첫 호출은 기준점만 잡는다
    for p in psutil.process_iter():
        try:
            p.cpu_percent(interval=None)
        except psutil.Error:
            pass
    time.sleep(0.5)
    while True:
        print(draw(), end="", flush=True)
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            if msvcrt.kbhit() and msvcrt.getwch().lower() == "q":
                os._exit(0)
            time.sleep(0.05)


if __name__ == "__main__":
    main()
