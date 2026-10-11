from datetime import datetime
from bridge import uploads
from bridge.config import KST

NOW = datetime(2026, 8, 4, 23, 30, tzinfo=KST)

def test_sanitize_strips_path_and_weird_chars():
    assert uploads.sanitize_filename("../../etc/passwd") == "passwd"
    assert uploads.sanitize_filename("스크린샷 2026.png") == "스크린샷_2026.png"
    assert uploads.sanitize_filename("") == "file"

def test_path_uses_kst_date(tmp_path):
    p = uploads.build_upload_path("hotel", "a.png", root=tmp_path, now=NOW)
    assert p == tmp_path / "hotel" / "2026-08-04" / "a.png"
    assert p.parent.is_dir()

def test_collision_appends_counter(tmp_path):
    first = uploads.build_upload_path("x", "a.png", root=tmp_path, now=NOW)
    first.write_bytes(b"1")
    second = uploads.build_upload_path("x", "a.png", root=tmp_path, now=NOW)
    assert second.name == "a-1.png"


# ---------- 폴더 드롭(하위 구조 유지) ----------

def test_sanitize_subdir_는_상위이동과_이상성분을_걷어낸다():
    assert uploads.sanitize_subdir("사진/여름") == "사진/여름"
    assert uploads.sanitize_subdir("../..//etc/passwd") == "etc/passwd"   # ..·빈 성분 소멸
    assert uploads.sanitize_subdir("a\\b") == "a/b"                       # 윈도우 구분자
    assert uploads.sanitize_subdir(".") == ""
    assert len(uploads.sanitize_subdir("a/" * 30 + "b").split("/")) == 10  # 깊이 상한


def test_build_upload_path_subdir_는_날짜폴더_아래에_구조를_만든다(tmp_path):
    p = uploads.build_upload_path("proj", "b.png", root=tmp_path, subdir="사진/여름")
    assert p.parent.name == "여름" and p.parent.parent.name == "사진"
    assert p.parent.is_dir()                              # mkdir 까지 끝난 상태
    assert p.name == "b.png"


# ---------- 캐시 비우기 (대표 지시 2026-08-15) ----------

def test_clear_는_날짜폴더만_지우고_감사로그는_남긴다(tmp_path):
    audit = tmp_path / "frogterminal-audit.log"      # 캐시와 같은 자리에 사는 감사로그
    audit.write_text("지우면 안 되는 기록")
    day = tmp_path / "wiki" / "2026-08-15"
    (day / "안").mkdir(parents=True)
    (day / "a.png").write_text("12345")
    (day / "안" / "b.png").write_text("678")
    keep = tmp_path / "wiki" / "메모.txt"             # 날짜폴더가 아닌 것은 손대지 않는다
    keep.write_text("x")

    assert uploads.usage(tmp_path) == {"bytes": 8, "files": 2}
    got = uploads.clear(tmp_path)
    assert got["files"] == 2 and got["bytes"] == 8
    assert not day.exists()
    assert audit.read_text() == "지우면 안 되는 기록"   # ★ 감사로그 보존
    assert keep.exists()                              # 낯선 파일도 보존
    assert uploads.usage(tmp_path) == {"bytes": 0, "files": 0}


def test_clear_는_빈_폴더에서도_안전하다(tmp_path):
    assert uploads.clear(tmp_path) == {"bytes": 0, "files": 0, "left": {"bytes": 0, "files": 0}}
