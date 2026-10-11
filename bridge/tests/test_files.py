"""파일 패널 API 가드 테스트 — 공격 경로(탈출·비밀파일·바이너리)를 실제 파일로 재현."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from bridge import config, files

NANO = 1754377200123456789               # 실제 st_mtime_ns 크기(19자리) — JS 안전정수(16자리)를 넘는다
PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"
IMAGE_SAMPLES = [
    ("png", "image/png", PNG),
    ("jpg", "image/jpeg", b"\xff\xd8\xff\xe0jpeg"),
    ("gif", "image/gif", b"GIF89aimage"),
    ("webp", "image/webp", b"RIFF\x04\x00\x00\x00WEBPimage"),
    ("avif", "image/avif", b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00mif1avif"),
    ("bmp", "image/bmp", b"BMimage"),
]


def _나노초로_맞춘다(p: Path, ns: int = NANO) -> None:
    os.utime(p, ns=(ns, ns))
    if p.stat().st_mtime_ns != ns:       # 1초 해상도 FS 에선 이 버그 자체가 재현되지 않는다
        pytest.skip("파일시스템이 나노초 mtime 을 보존하지 않는다")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "work"
    p = root / "alpha"
    (p / "docs").mkdir(parents=True)
    (p / "README.md").write_text("# 알파\n")
    (p / "docs" / "노트.md").write_text("메모")
    (p / ".env").write_text("SECRET=1")
    (p / "키.pem").write_text("PRIVATE")
    (p / ".git").mkdir()
    (p / ".git" / "config").write_text("[remote] url=https://tok@x")
    (p / "img.png").write_bytes(PNG)
    (p / ".envrc").write_text("export AWS_SECRET_ACCESS_KEY=abc123")   # direnv — 경계 매치가 놓쳤다
    (p / ".env.local").write_text("SUPABASE_SERVICE_KEY=zzz")
    (p / ".env.bak").write_text("OLD=1")
    (p / "credentials-backup.json").write_text('{"token":"t"}')
    (p / "secrets-2024.txt").write_text("pw")
    (tmp_path / "밖.txt").write_text("탈출 대상")
    (tmp_path / "밖폴더").mkdir()
    os.symlink(p / ".env", p / "link_to_env.txt")             # 루트 안 비밀파일을 가리키는 심링크
    os.symlink("/etc/hosts", p / "link_to_etc_hosts.txt")     # 루트 밖 파일
    os.symlink(tmp_path / "밖폴더", p / "link_to_outside_dir")  # 루트 밖 디렉터리
    monkeypatch.setattr(config, "projects_root", lambda: root)
    monkeypatch.setattr(files.sessions, "is_valid_session", lambda n: n == "alpha")
    return p


def test_make_dir_는_읽기와_같은_잣대로_만든다(proj):
    """새 폴더(파일 패널 📁) — 정상 생성 / 중복 / 탈출·비밀이름·루트는 전부 거부."""
    files.make_dir("alpha", "docs/새폴더")
    assert (proj / "docs" / "새폴더").is_dir()
    with pytest.raises(FileExistsError):
        files.make_dir("alpha", "docs/새폴더")
    with pytest.raises(FileNotFoundError):                 # 부모가 없다 — 한 번에 한 단계만
        files.make_dir("alpha", "없는부모/자식")
    for bad in ("", "/", "../탈출", ".git/hooks", ".env백업", "docs/../..", "secret-폴더"):
        with pytest.raises(files.NotAllowed):
            files.make_dir("alpha", bad)
    with pytest.raises(files.NotAllowed):                  # 남의 프로젝트 이름
        files.make_dir("beta", "폴더")


def test_list_dir_비밀파일과_git은_안보임(proj):
    names = [e["name"] for e in files.list_dir("alpha")]
    assert names == ["docs", "README.md", "img.png"]      # 폴더 먼저, .env/.pem/.git 제외


def test_list_dir_하위폴더(proj):
    assert files.list_dir("alpha", "docs") == [
        {"name": "노트.md", "dir": False, "size": len("메모".encode())}]


def test_read_text(proj):
    got = files.read_text("alpha", "README.md")
    assert got["content"] == "# 알파\n" and got["mtimeNs"].isdigit()   # 버전 토큰은 문자열


@pytest.mark.parametrize("rel", ["../밖.txt", "..%2F밖.txt", "/etc/hosts", ".env", "키.pem",
                                 ".git/config", "docs/../../밖.txt"])
def test_탈출과_비밀파일은_전부_차단(proj, rel):
    with pytest.raises(files.NotAllowed):
        files.safe_path("alpha", rel)


def test_심링크로도_못_나간다(proj, tmp_path):
    os.symlink(tmp_path / "밖.txt", proj / "링크.txt")
    with pytest.raises(files.NotAllowed):
        files.read_text("alpha", "링크.txt")


@pytest.mark.parametrize("rel", [".GIT/config", ".Git/config", ".GIT", ".Git",
                                 ".ENV", ".Env.Local", ".ENVRC", "키.PEM", "ID_RSA"])
def test_대소문자_변형으로도_못_뚫는다(proj, rel):
    with pytest.raises(files.NotAllowed):     # macOS 는 대소문자 무시 FS — .GIT 은 곧 .git 이다
        files.read_text("alpha", rel)


@pytest.mark.parametrize("rel", [".git", ".GIT", ".Git"])
def test_git_폴더는_목록도_안_보여준다(proj, rel):
    with pytest.raises(files.NotAllowed):
        files.list_dir("alpha", rel)


@pytest.mark.parametrize("rel", [".envrc", ".env.local", ".env.bak",
                                 "credentials-backup.json", "secrets-2024.txt",
                                 "auth.json", "oauth.json", "gh_token.json", "token.json"])   # 코덱스 교차점검 2026-10-11
def test_denylist_경계_밖_비밀파일도_차단(proj, rel):
    with pytest.raises(files.NotAllowed):     # 경계 매치 시절 .envrc·credentials-backup 이 샜다
        files.read_text("alpha", rel)


def test_list_dir_은_비밀파일_변형도_안_보여준다(proj):
    names = [e["name"] for e in files.list_dir("alpha")]
    assert not [n for n in names
                if "env" in n.lower() or "credential" in n.lower() or "secret" in n.lower()]


def test_list_dir_심링크는_대상_메타데이터를_안_흘린다(proj):
    entries = files.list_dir("alpha")
    assert not [e for e in entries if e["name"].startswith("link_to")]   # 존재 자체를 안 알린다
    assert 213 not in [e["size"] for e in entries]                       # /etc/hosts 크기 유출 금지
    for name in ("link_to_env.txt", "link_to_etc_hosts.txt", "link_to_outside_dir"):
        with pytest.raises(files.NotAllowed):                            # read_text 와 같은 판정
            files.read_text("alpha", name)


def test_루트_안_평범한_심링크는_그대로_보인다(proj):
    os.symlink(proj / "README.md", proj / "링크_readme.md")               # 과잉 차단 방지 회귀
    got = [e for e in files.list_dir("alpha") if e["name"] == "링크_readme.md"]
    assert got == [{"name": "링크_readme.md", "dir": False, "size": len("# 알파\n".encode())}]


def test_gitignore도_함께_막힌다(proj):
    (proj / ".gitignore").write_text("node_modules\n")
    with pytest.raises(files.NotAllowed):     # .git 접두 통째 차단의 의도된 대가 — fail-closed
        files.read_text("alpha", ".gitignore")


def test_이름에_env가_들어간_평범한_파일은_안_막힌다(proj):
    (proj / "environment-notes.md").write_text("설정 메모")               # 점 없는 env 는 정상 파일
    got = files.read_text("alpha", "environment-notes.md")
    assert got["content"] == "설정 메모"
    assert "environment-notes.md" in [e["name"] for e in files.list_dir("alpha")]


def test_바이너리는_거부(proj):
    with pytest.raises(files.IsBinary):
        files.read_text("alpha", "img.png")


def test_read_image_는_매직으로_확정한_타입과_바이트를_돌려준다(proj):
    got = files.read_image("alpha", "img.png")
    assert got == {"content": PNG, "contentType": "image/png",
                   "size": len(PNG), "name": "img.png"}


@pytest.mark.parametrize("ext, content_type, data", IMAGE_SAMPLES)
def test_read_image_지원형식의_Content_Type은_매직과_정확히_맞는다(
        proj, ext, content_type, data):
    name = f"sample.{ext}"
    (proj / name).write_bytes(data)
    got = files.read_image("alpha", name)
    assert got["contentType"] == content_type and got["content"] == data


def test_read_image_는_확장자와_내용이_다르면_거부한다(proj):
    (proj / "가짜.png").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    with pytest.raises(files.UnsupportedImage, match="실제 파일 형식"):
        files.read_image("alpha", "가짜.png")
    (proj / "진짜.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    (proj / "png인척.png").symlink_to(proj / "진짜.jpg")
    with pytest.raises(files.UnsupportedImage, match="실제 파일 형식"):
        files.read_image("alpha", "png인척.png")     # resolve된 목적지 확장자로 우회하면 안 된다


@pytest.mark.parametrize("name, reason", [
    ("그림.svg", "안전 문제"),
    ("사진.heic", "브라우저에서 바로 표시"),
])
def test_read_image_는_SVG와_HEIC를_사람말로_거부한다(proj, name, reason):
    (proj / name).write_bytes(b"<svg></svg>" if name.endswith("svg") else b"\x00\x00\x00\x18ftypheic")
    with pytest.raises(files.UnsupportedImage, match=reason):
        files.read_image("alpha", name)


def test_read_image_는_이미지_전용_상한을_지킨다(proj, monkeypatch):
    monkeypatch.setattr(config, "MAX_IMAGE_FILE_BYTES", len(PNG) - 1)
    with pytest.raises(files.TooLarge):
        files.read_image("alpha", "img.png")


CP949_CSV = "이름,금액\n김철수,1000\n".encode("cp949")   # 한국 엑셀·옛 .txt 가 쓰는 인코딩


def test_비UTF8_파일은_열리지_않는다(proj):
    """★ errors='replace' 로 보여주면 그 화면을 저장하는 순간 원본이 복구 불가로 망가진다.

    실측: CP949 40바이트를 열면 '이름'이 U+FFFD 로 바뀌고, 저장하면 77바이트가 되며
    cp949 디코딩조차 실패한다. NUL 이 없어 바이너리 판정은 통과하므로 여기서 막아야 한다.
    """
    p = proj / "매출.csv"
    p.write_bytes(CP949_CSV)
    with pytest.raises(files.IsBinary):      # 415 — 웹은 "글자 파일이 아니라 열 수 없습니다"
        files.read_text("alpha", "매출.csv")
    assert p.read_bytes() == CP949_CSV       # 거절했으니 원본은 그대로


def test_8KB_뒤에_숨은_비UTF8_바이트도_잡는다(proj):
    """거의 ASCII 인 파일에 섞인 cp1252 따옴표 한 글자 — 눈에 안 띄어 더 위험하다."""
    p = proj / "긴문서.md"
    p.write_bytes(b"a" * 9000 + "’".encode("cp1252") + b"\n")
    with pytest.raises(files.IsBinary):
        files.read_text("alpha", "긴문서.md")


def test_감사로그_이름은_어디서도_안_열린다(proj):
    """_uploads 차단(sessions)에 더한 이중 방어 — 감사로그가 어디에 있든 이름으로 막는다."""
    (proj / "frogterminal-audit.log").write_text("2026-08-05T00:00:00+09:00 attach user=사용자\n")
    with pytest.raises(files.NotAllowed):
        files.read_text("alpha", "frogterminal-audit.log")
    assert "frogterminal-audit.log" not in [e["name"] for e in files.list_dir("alpha")]


def test_없는_프로젝트는_거부(proj):
    with pytest.raises(files.NotAllowed):
        files.list_dir("beta")


def test_write_text_저장과_mtime(proj):
    base = files.read_text("alpha", "README.md")["mtimeNs"]
    new = files.write_text("alpha", "README.md", "# 수정\n", base)
    assert files.read_text("alpha", "README.md")["content"] == "# 수정\n"
    assert isinstance(new, str) and int(new) > int(base)   # 토큰은 문자열, 대소는 정수로 본다


def test_write_text_mtime_불일치는_conflict(proj):
    with pytest.raises(files.Conflict):
        files.write_text("alpha", "README.md", "x", 1)


def test_write_text_신규생성·바이너리·비밀은_거부(proj):
    base = files.read_text("alpha", "README.md")["mtimeNs"]
    with pytest.raises(files.NotAllowed):
        files.write_text("alpha", "새파일.md", "x", "0")
    with pytest.raises(files.IsBinary):
        files.write_text("alpha", "img.png", "x", "0")
    with pytest.raises(files.NotAllowed):
        files.write_text("alpha", ".env", "x", base)


def test_버전토큰은_JS_왕복에도_무손실(proj):
    """★ st_mtime_ns 는 19자리 — JSON 숫자로 보내면 브라우저가 반올림해 저장이 영영 409 가 된다."""
    _나노초로_맞춘다(proj / "README.md")
    got = files.read_text("alpha", "README.md")
    v = got["mtimeNs"]
    assert isinstance(v, str) and int(v) > 2 ** 53            # JS Number 안전범위 밖임을 못박는다
    assert json.loads(json.dumps(got))["mtimeNs"] == v        # 브라우저 JSON.parse → 재전송 왕복
    files.write_text("alpha", "README.md", "새 내용\n", v)     # 왕복한 값 그대로 저장되어야 한다
    assert files.read_text("alpha", "README.md")["content"] == "새 내용\n"


@pytest.mark.parametrize("뭉갠값", ["1754377200123456768",     # 배정밀도 왕복이 만드는 실제 비트값
                                    "1754377200123456800"])    # node/브라우저가 그 값을 찍는 표기
def test_JS_반올림_흉내낸_토큰은_conflict(proj, 뭉갠값):
    """이 버그의 본질 — 뭉개진 값은 남이 고친 것과 구별되지 않으니 저장을 막아야 한다.

    두 값 모두 실측이다: node 로 JSON.parse(19자리 숫자) 하면 …789 가 …800 으로 찍힌다.
    """
    _나노초로_맞춘다(proj / "README.md")                        # mtime = NANO 로 확정
    v = files.read_text("alpha", "README.md")["mtimeNs"]
    assert v == str(NANO) and 뭉갠값 != v
    with pytest.raises(files.Conflict):
        files.write_text("alpha", "README.md", "x", 뭉갠값)


def test_옛_정수_토큰은_터지지_않고_conflict(proj):
    base = files.read_text("alpha", "README.md")["mtimeNs"]
    with pytest.raises(files.Conflict):                       # 문자열 계약 — 정수는 500 이 아니라 거절
        files.write_text("alpha", "README.md", "x", int(base))


def test_frogtmp_잔재는_안_보이고_안_읽힌다(proj):
    """SIGKILL 로 남은 원자적 쓰기 잔재 — 목록·읽기 모두에서 빠져야 한다."""
    (proj / "README.md.frogtmp").write_text("죽다 만 임시본")
    assert "README.md.frogtmp" not in [e["name"] for e in files.list_dir("alpha")]
    with pytest.raises(files.NotAllowed):
        files.read_text("alpha", "README.md.frogtmp")


def test_gitignore가_frogtmp_잔재를_무시한다():
    """문자열 매치가 아니라 git 에게 직접 묻는다 — 패턴 뒤 주석 같은 건 문자열로는 안 잡힌다."""
    repo = Path(__file__).resolve().parents[2]
    done = subprocess.run(["git", "check-ignore", "-q", "README.md.frogtmp"], cwd=repo)
    assert done.returncode == 0, "잔재가 git status 를 더럽힌다"   # 0=무시됨, 1=추적 대상


@pytest.mark.parametrize("name", [" .env", "\t.env", " .git", " 키.pem", ".env ", " credentials.json"])
def test_앞뒤_공백_붙은_이름도_차단(proj, name):
    """'.env ' 는 막히고 ' .env' 는 새던 비대칭 — 판정 전에 이름을 다듬는다."""
    (proj / name).write_text("SECRET=1")
    with pytest.raises(files.NotAllowed):
        files.read_text("alpha", name)
    assert name not in [e["name"] for e in files.list_dir("alpha")]


# ---------- '맥에서 열기' 절대경로 판정 (어디 있든 열리게) ----------
# ★의도된 예외다: 이 화면은 터미널이라 `open <경로>` 를 직접 칠 수 있어 폴더 제한이 방어가
#   아니라 불편만 된다. 대신 '읽어서 브라우저로 보내는' API 의 제한은 그대로 살아 있어야 한다 —
#   그 계약은 아래 test_열기_허용이_읽기_제한을_풀지_않는다 가 지킨다.

def test_resolve_openable_은_실재하는_절대경로면_어디든_연다(tmp_path):
    outside = tmp_path / "밖" / "다운로드" / "사진.jpg"
    outside.parent.mkdir(parents=True)
    outside.write_text("img")
    assert files.resolve_openable(str(outside)) == outside.resolve()
    link = tmp_path / "바로가기.jpg"
    link.symlink_to(outside)
    assert files.resolve_openable(str(link)) == outside.resolve()     # 심링크는 목적지로


def test_resolve_openable_은_없는_파일과_상대경로는_거부한다(tmp_path):
    for bad in (str(tmp_path / "없다.jpg"), "상대경로.md", "", "/tmp/\x00"):
        with pytest.raises(files.NotAllowed):
            files.resolve_openable(bad)


def test_열기_허용이_읽기_제한을_풀지_않는다(tmp_path, monkeypatch):
    """열기(맥 화면)와 읽기(브라우저로 전송)는 다른 문이다 — 읽기는 프로젝트·비밀파일 제한 유지."""
    proj = tmp_path / "projects" / "wiki"
    proj.mkdir(parents=True)
    (proj / ".env").write_text("SECRET=1")
    monkeypatch.setattr(files.config, "projects_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(files.sessions, "is_valid_session", lambda n, root=None: True)
    with pytest.raises(files.NotAllowed):
        files.read_text("wiki", ".env")                               # 비밀파일 읽기는 여전히 차단
    with pytest.raises(files.NotAllowed):
        files.read_text("wiki", "../../밖.md")                        # 밖으로 나가는 읽기도 차단
