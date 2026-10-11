"""원본 찾기(bridge/locate.py) — mdfind 는 몽키패치하고 판정 로직만 검증한다."""
import os
import unicodedata
from pathlib import Path

from bridge import locate


def test_find_file_은_크기와_수정시각이_다_맞아야_한다(tmp_path, monkeypatch):
    a = tmp_path / "대본.md"
    a.write_text("하나")
    other = tmp_path / "다른"
    other.mkdir()
    c = other / "대본.md"
    c.write_text("하나둘셋넷")                            # 크기가 다르다
    monkeypatch.setattr(locate, "_candidates", lambda name: [a, c])
    st = a.stat()
    assert locate.find_file("대본.md", st.st_size, int(st.st_mtime * 1000)) == [str(a)]


def test_find_file_진짜_사본이_둘이면_특정하지_않는다(tmp_path, monkeypatch):
    """같은 크기·같은 수정시각이 두 곳이면 어느 쪽인지 알 수 없다 — 웹이 업로드로 물러선다."""
    a, b = tmp_path / "대본.md", tmp_path / "사본.d"
    b.mkdir()
    b = b / "대본.md"
    a.write_text("같음")
    b.write_text("같음")
    os.utime(b, (a.stat().st_atime, a.stat().st_mtime))
    monkeypatch.setattr(locate, "_candidates", lambda name: [a, b])
    st = a.stat()
    assert len(locate.find_file("대본.md", st.st_size, int(st.st_mtime * 1000))) == 2


def test_find_dir_은_자식_목록으로_특정한다(tmp_path, monkeypatch):
    d1 = tmp_path / "사진"
    d1.mkdir()
    (d1 / "a.png").write_text("x")
    (d1 / "b.png").write_text("y")
    d2 = tmp_path / "딴곳" / "사진"
    d2.mkdir(parents=True)
    (d2 / "c.png").write_text("z")
    monkeypatch.setattr(locate, "_candidates", lambda name: [d1, d2])
    assert locate.find_dir("사진", ["a.png", "b.png"]) == [str(d1)]
    assert locate.find_dir("사진", []) == []              # 빈 폴더는 특정할 근거가 없다


def test_candidates_는_업로드_캐시를_배제한다(monkeypatch):
    """_uploads 의 사본이 원본 행세를 하면 이 기능의 목적이 죽는다."""
    from bridge import config

    class R:
        returncode = 0
        stdout = f"{config.upload_root()}/proj/2026-08-15/대본.md\n/Users/x/대본.md\n/Users/y/다른이름.md\n"

    monkeypatch.setattr(locate.subprocess, "run", lambda *a, **k: R())
    assert [str(p) for p in locate._candidates("대본.md")] == ["/Users/x/대본.md"]


def test_한글_파일명이_확장자와_함께_와도_원본을_찾는다(tmp_path, monkeypatch):
    nfd_name = unicodedata.normalize("NFD", "완성본.mp4")
    original = tmp_path / nfd_name
    original.write_bytes(b"original")

    class R:
        returncode = 0
        stdout = f"{original}\n"

    def run(args, **kwargs):
        assert args == ["mdfind", "-name", "완성본"]
        return R()

    monkeypatch.setattr(locate.subprocess, "run", run)
    st = original.stat()
    assert locate.find_file("완성본.mp4", st.st_size, int(st.st_mtime * 1000)) == [str(original)]


def test_NFD_NFC_가_달라도_같은_파일로_본다(monkeypatch):
    nfd_name = unicodedata.normalize("NFD", "완성본.mp4")

    class R:
        returncode = 0
        stdout = f"/Users/x/{nfd_name}\n"

    monkeypatch.setattr(locate.subprocess, "run", lambda *a, **k: R())
    assert [str(p) for p in locate._candidates("완성본.mp4")] == [f"/Users/x/{nfd_name}"]


def test_한글_폴더도_NFD_자식_이름으로_찾는다(tmp_path, monkeypatch):
    folder = tmp_path / unicodedata.normalize("NFD", "완성본.자료")
    folder.mkdir()
    (folder / unicodedata.normalize("NFD", "대본.md")).write_text("하나")
    (folder / unicodedata.normalize("NFD", "설정.json")).write_text("{}")

    class R:
        returncode = 0
        stdout = f"{folder}\n"

    def run(args, **kwargs):
        assert args == ["mdfind", "-name", "완성본"]
        return R()

    monkeypatch.setattr(locate.subprocess, "run", run)

    assert locate.find_dir("완성본.자료", ["대본.md", "설정.json"]) == [str(folder)]


# ---------- 화면 캡처 원본 찾기 (붙여넣기 사본을 없애는 길) ----------

def test_screenshot_dir_은_시스템_설정을_읽는다():
    """`defaults read` 는 한글 폴더명을 \\Uxxxx 로 뭉개서 내준다 — plutil 경로여야 온전하다."""
    d = locate.screenshot_dir()
    assert d.is_absolute() and not str(d).startswith("~")


def test_recent_capture_는_크기가_맞는_최신_파일만_준다(tmp_path, monkeypatch):
    import shutil
    import time as _t
    src = Path(__file__).parent.parent.parent / "web" / "icon-192.png"   # 실제 192x192 PNG
    shot = tmp_path / "스크린샷 2026-08-15 오후 3.00.00.png"
    shutil.copy(src, shot)
    os.utime(shot, (_t.time(), _t.time()))
    monkeypatch.setattr(locate, "screenshot_dir", lambda: tmp_path)

    assert locate.recent_capture(192, 192) == str(shot)       # 크기 일치 — 이 경로를 쓴다
    assert locate.recent_capture(800, 600) is None            # 웹에서 복사한 다른 그림이면 안 걸린다
    os.utime(shot, (_t.time() - 600, _t.time() - 600))        # 10분 전 캡처
    assert locate.recent_capture(192, 192) is None            # 시간창 밖 — 엉뚱한 옛 캡처 방지
