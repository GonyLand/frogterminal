"""대화 맥락 추출 테스트 — 클로드 코드 transcript(JSONL)에서 asked/said 를 뽑는다.

카드에 "무엇을 시켰고 클로드가 뭐라 했는지"가 없으면 결재를 할 수 없다. 여기가 그 계약.
"""
import json

import pytest

from bridge import config, decisions


# 맥락을 못 읽었을 때의 값 — 세 필드가 '항상 있다'가 계약이다(웹이 undefined 분기를 안 만들게).
# lastAt = 마지막 대화 시각. 브릿지가 "이 결재는 이미 지나갔나"를 재는 눈금이다(decisions._behind).
EMPTY = {"asked": "", "said": "", "lastAt": ""}


def line(type_, content, **extra):
    return json.dumps({"type": type_, "message": {"role": type_, "content": content},
                       **extra}, ensure_ascii=False)


@pytest.fixture
def transcripts(tmp_path, monkeypatch):
    """~/.claude/projects 를 tmp 로 갈아끼우고, 카드용 transcript 를 만들어 준다."""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(config, "TRANSCRIPT_ROOT", root)
    decisions.forget_context()                    # 테스트끼리 캐시가 새지 않게

    def _write(cwd, sid, lines):
        d = root / decisions.encode_cwd(cwd)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{sid}.jsonl"
        p.write_text("\n".join(lines) + "\n")
        return p
    return _write


def card(cwd="/Users/x/proj", sid="s1", **extra):
    return {"sessionId": sid, "cwd": cwd, "kind": "idle", "project": "proj", **extra}


# ── 경로 계산 ────────────────────────────────────────────────────────────────

def test_encode_cwd_는_슬래시와_점을_모두_하이픈으로():
    """실측 7건으로 확인한 규칙 — '/.' 는 '--' 가 된다."""
    assert decisions.encode_cwd("/Users/someone/work/alpha") == \
        "-Users-someone-work-alpha"
    assert decisions.encode_cwd("/Users/someone/.hermes/scripts") == \
        "-Users-someone--hermes-scripts"


def test_훅이_준_transcript_path_를_우선_쓴다(transcripts, tmp_path):
    """계산보다 정확하다 — 실측으로 payload 에 transcript_path 가 온다."""
    p = transcripts("/other/place", "s9", [line("user", "훅 경로로 찾은 대화")])
    ctx = decisions.context_for(card(cwd="/전혀/다른/곳", sid="s9", transcriptPath=str(p)))
    assert ctx["asked"] == "훅 경로로 찾은 대화"


def test_transcript_root_밖을_가리키면_거부한다(transcripts, tmp_path):
    outside = tmp_path / "secret.jsonl"
    outside.write_text(line("user", "읽히면 안 된다") + "\n")
    ctx = decisions.context_for(card(transcriptPath=str(outside)))
    assert ctx == EMPTY


def test_sessionId_가_None_인_화면스캔_카드는_조용히_빈값(transcripts):
    ctx = decisions.context_for({"sessionId": None, "cwd": "", "kind": "picker"})
    assert ctx == EMPTY


# ── 추출 규칙 ────────────────────────────────────────────────────────────────

def test_마지막_user_발화와_마지막_assistant_글을_뽑는다(transcripts):
    transcripts("/Users/x/proj", "s1", [
        line("user", "옛날에 시킨 것"),
        line("assistant", "옛날 답"),
        line("user", "어 저장하면된다. 공문양식은 다시 만들꺼야"),
        line("assistant", "볼트에 저장했습니다. 다음은 무엇을 할까요?"),
    ])
    ctx = decisions.context_for(card())
    assert ctx["asked"] == "어 저장하면된다. 공문양식은 다시 만들꺼야"
    assert ctx["said"] == "볼트에 저장했습니다. 다음은 무엇을 할까요?"


def test_isSidechain_은_건너뛴다(transcripts):
    """서브에이전트 대화 — 사용자가 시킨 것이 아니다."""
    transcripts("/Users/x/proj", "s1", [
        line("user", "사용자가 진짜 시킨 것"),
        line("assistant", "사용자에게 한 진짜 말"),
        line("user", "서브에이전트에게 준 지시", isSidechain=True),
        line("assistant", "서브에이전트가 한 말", isSidechain=True),
    ])
    ctx = decisions.context_for(card())
    assert ctx["asked"] == "사용자가 진짜 시킨 것"
    assert ctx["said"] == "사용자에게 한 진짜 말"


def test_슬래시명령_껍데기와_인터럽트_표식은_건너뛴다(transcripts):
    transcripts("/Users/x/proj", "s1", [
        line("user", "진짜 지시"),
        line("user", "<command-name>/clear</command-name>"),
        line("user", "<local-command-stdout>Catch you later!</local-command-stdout>"),
        line("user", "[Request interrupted by user for tool use]"),
    ])
    assert decisions.context_for(card())["asked"] == "진짜 지시"


def test_압축_부산물은_lastAt_을_밀지_않는다(transcripts):
    """★ /compact 뒤 사이드바가 '진행중…'에 묶인 진짜 원인 (실측 2026-08-28 donworksmall).

    압축은 대화 파일에 자기 부산물(명령·요약 주입·완료 출력)을 남기는데, 그 시각이 마지막 Stop
    훅보다 한참 뒤다. 이걸 '대화가 앞서 갔다'로 세면 끝난 카드가 _behind 로 뒤집혀, 압축 한 번에
    멀쩡히 쉬는 세션이 진행중으로 보인다. 실제 항목 5개를 그대로 재현한다.
    """
    transcripts("/Users/x/proj", "s1", [
        line("assistant", "다 끝냈습니다", timestamp="2026-08-28T10:35:10Z"),
        line("user", "/compact", timestamp="2026-08-28T10:42:56Z"),
        line("user", "<command-name>/compact</command-name>", timestamp="2026-08-28T10:42:56Z"),
        line("user", "This session is being continued…", isCompactSummary=True,
             timestamp="2026-08-28T10:45:14Z"),
        line("user", "<local-command-stdout>Compacted</local-command-stdout>",
             timestamp="2026-08-28T10:45:15Z"),
    ])
    ctx = decisions.context_for(card())
    assert ctx["lastAt"] == "2026-08-28T10:35:10Z"          # 압축 전 마지막 진짜 대화
    assert ctx["said"] == "다 끝냈습니다"
    assert "continued" not in ctx["asked"]                   # 요약 주입이 '시킨 것'으로 오르지 않게
    done = {"kind": "done", "updatedAt": "2026-08-28T19:35:10+09:00", **ctx}
    assert decisions._behind(done) is False                  # 끝난 카드는 그대로 끝난 채로


def test_승인_뒤_이어진_진짜_진행은_그대로_잡는다(transcripts):
    """반대쪽 보증 — 터미널에서 직접 승인해 일이 계속되면 결재 카드는 내려가야 한다."""
    transcripts("/Users/x/proj", "s1", [
        line("assistant", "권한 주세요", timestamp="2026-08-28T10:35:10Z"),
        line("user", [{"type": "tool_result", "content": "명령 출력"}],
             timestamp="2026-08-28T10:40:00Z"),
    ])
    ctx = decisions.context_for(card())
    assert ctx["lastAt"] == "2026-08-28T10:40:00Z"
    stale = {"kind": "permission", "updatedAt": "2026-08-28T19:35:10+09:00", **ctx}
    assert decisions._behind(stale) is True


def test_content_배열이면_text_블록만_모은다(transcripts):
    """도구 호출·생각 블록은 글이 아니다. tool_result 만 든 user 항목은 통째로 건너뛴다."""
    transcripts("/Users/x/proj", "s1", [
        line("user", "진짜 지시"),
        line("assistant", [{"type": "thinking", "thinking": "속으로 생각"},
                           {"type": "text", "text": "앞부분"},
                           {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                           {"type": "text", "text": "뒷부분"}]),
        line("user", [{"type": "tool_result", "content": "명령 출력"}]),
    ])
    ctx = decisions.context_for(card())
    assert ctx["asked"] == "진짜 지시"
    assert ctx["said"] == "앞부분 뒷부분"
    assert "생각" not in ctx["said"] and "ls" not in ctx["said"]


def test_줄바꿈은_한_줄로_접는다(transcripts):
    transcripts("/Users/x/proj", "s1", [line("user", "첫 줄\n\n둘째   줄\t셋째")])
    assert decisions.context_for(card())["asked"] == "첫 줄 둘째 줄 셋째"


# ── 165MB 대응: 꼬리만 읽는다 ────────────────────────────────────────────────

def test_큰_파일은_꼬리만_읽는다(transcripts, monkeypatch):
    """★ 실측 165MB 파일이 존재한다 — 통째로 읽으면 폴링 한 번에 브릿지가 죽는다.

    앞부분에 '읽으면 안 되는' 표식을 심고, 꼬리 창(TRANSCRIPT_TAIL_BYTES)을 훨씬 넘게
    부풀린 뒤 결과에 표식이 섞이지 않는지 본다.
    """
    monkeypatch.setattr(config, "TRANSCRIPT_TAIL_BYTES", 4096)
    filler = line("assistant", "가" * 500)
    transcripts("/Users/x/proj", "s1",
                [line("user", "읽으면안되는표식"), line("assistant", "읽으면안되는표식")]
                + [filler] * 40
                + [line("user", "꼬리에_있는_진짜_지시"), line("assistant", "꼬리에_있는_진짜_답")])
    ctx = decisions.context_for(card())
    assert ctx["asked"] == "꼬리에_있는_진짜_지시"
    assert ctx["said"] == "꼬리에_있는_진짜_답"
    assert "읽으면안되는표식" not in ctx["asked"] + ctx["said"]


def test_꼬리_경계에서_잘린_첫_줄은_버린다(transcripts, monkeypatch):
    """seek 한 지점의 첫 줄은 반쪽 JSON 이다 — 파싱 실패로 흘리지 말고 아예 버린다."""
    monkeypatch.setattr(config, "TRANSCRIPT_TAIL_BYTES", 200)
    transcripts("/Users/x/proj", "s1", [
        line("user", "나" * 300),                 # 창보다 길어 반드시 중간이 잘린다
        line("assistant", "온전한 답"),
    ])
    ctx = decisions.context_for(card())
    assert ctx["said"] == "온전한 답"
    assert ctx["asked"] == ""                     # 잘린 줄을 억지로 살려내지 않는다


# ── 길이 상한 ────────────────────────────────────────────────────────────────

def test_길이_상한이_적용되고_잘린_표시가_남는다(transcripts, monkeypatch):
    monkeypatch.setattr(config, "CARD_CONTEXT_CHARS", 20)
    transcripts("/Users/x/proj", "s1", [line("user", "가" * 100)])
    asked = decisions.context_for(card())["asked"]
    assert asked == "가" * 20 + "…"
    assert len(asked) == 21


# ── 없어도 죽지 않는다 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("lines", [
    [],                                            # 텅 빈 파일
    ["{깨진 JSON", "또 깨짐]"],                    # 전부 파싱 실패
    [line("system", "글이 아닌 항목")],            # user/assistant 가 아예 없음
    [json.dumps({"type": "user", "message": "문자열이_아닌_메시지_아님"})],
    [json.dumps(["객체가", "아닌", "줄"])],
])
def test_망가진_transcript_에도_카드는_산다(transcripts, lines):
    transcripts("/Users/x/proj", "s1", lines)
    assert decisions.context_for(card()) == EMPTY


def test_파일이_없으면_빈값(transcripts):
    assert decisions.context_for(card(sid="없는세션")) == EMPTY


def test_읽을_권한이_없어도_빈값(transcripts):
    p = transcripts("/Users/x/proj", "s1", [line("user", "못 읽는다")])
    p.chmod(0o000)
    try:
        assert decisions.context_for(card()) == EMPTY
    finally:
        p.chmod(0o644)


# ── 캐시 (폴링 7초 × 카드 7장 = 매번 파일 7개 꼬리 읽기) ─────────────────────

def test_mtime_이_그대로면_다시_파싱하지_않는다(transcripts, monkeypatch):
    transcripts("/Users/x/proj", "s1", [line("user", "한 번만 읽어라")])
    reads = []
    real = decisions._read_tail
    monkeypatch.setattr(decisions, "_read_tail", lambda p: (reads.append(p), real(p))[1])
    first = decisions.context_for(card())
    second = decisions.context_for(card())
    assert first == second == {"asked": "한 번만 읽어라", "said": "", "lastAt": ""}
    assert len(reads) == 1                        # 두 번째는 캐시


def test_파일이_바뀌면_다시_읽는다(transcripts):
    transcripts("/Users/x/proj", "s1", [line("user", "옛날 지시")])
    assert decisions.context_for(card())["asked"] == "옛날 지시"
    transcripts("/Users/x/proj", "s1", [line("user", "새 지시")])
    assert decisions.context_for(card())["asked"] == "새 지시"


# ── overview() 통합 ──────────────────────────────────────────────────────────

def test_overview_카드에_대화맥락이_실린다(tmp_path, monkeypatch, transcripts):
    d = tmp_path / "decisions"
    d.mkdir()
    monkeypatch.setattr(config, "decisions_dir", lambda: d)
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["proj"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: "")
    transcripts("/Users/x/proj", "s1", [
        line("user", "쇼츠 만들어줘"),
        line("assistant", "썸네일 문구를 A/B 중 무엇으로 할까요?"),
    ])
    (d / "s1.json").write_text(json.dumps(
        {"sessionId": "s1", "inTmux": True, "kind": "idle", "project": "proj", "cwd": "/Users/x/proj",
         "machine": "m", "message": "Claude is waiting for your input",
         "updatedAt": "2026-08-05T12:00:00+09:00"}))
    [c] = decisions.overview()["cards"]
    assert c["asked"] == "쇼츠 만들어줘"
    assert c["said"] == "썸네일 문구를 A/B 중 무엇으로 할까요?"
    assert c["message"] == "Claude is waiting for your input"    # 훅 원문은 그대로


def test_overview_맥락이_없어도_두_필드는_항상_있다(tmp_path, monkeypatch, transcripts):
    """웹이 c.asked 를 무조건 읽을 수 있어야 한다 — undefined 분기를 만들지 않는다."""
    d = tmp_path / "decisions"
    d.mkdir()
    monkeypatch.setattr(config, "decisions_dir", lambda: d)
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["proj"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: "")
    (d / "s1.json").write_text(json.dumps(
        {"sessionId": "s1", "inTmux": True, "kind": "idle", "project": "proj", "cwd": "/없는/경로",
         "machine": "m", "message": "m", "updatedAt": "2026-08-05T12:00:00+09:00"}))
    [c] = decisions.overview()["cards"]
    assert c["asked"] == "" and c["said"] == ""


def test_overview_맥락_추출이_터져도_목록은_산다(tmp_path, monkeypatch):
    """전역 제약 — 카드 하나가 전체 목록을 죽이면 안 된다."""
    d = tmp_path / "decisions"
    d.mkdir()
    monkeypatch.setattr(config, "decisions_dir", lambda: d)
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["proj"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: "")
    monkeypatch.setattr(decisions, "context_for",
                        lambda c: (_ for _ in ()).throw(RuntimeError("펑")))
    (d / "s1.json").write_text(json.dumps(
        {"sessionId": "s1", "inTmux": True, "kind": "idle", "project": "proj", "cwd": "/Users/x/proj",
         "machine": "m", "message": "m", "updatedAt": "2026-08-05T12:00:00+09:00"}))
    [c] = decisions.overview()["cards"]
    assert c["asked"] == "" and c["said"] == ""


def test_overview_화면스캔_피커카드는_맥락없이_정상_출력(tmp_path, monkeypatch):
    d = tmp_path / "decisions"
    d.mkdir()
    monkeypatch.setattr(config, "decisions_dir", lambda: d)
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["proj"])
    monkeypatch.setattr(decisions, "capture_pane",
                        lambda name: "무엇을 할까요?\n❯ 1. 예\n  2. 아니오\n")
    [c] = decisions.overview()["cards"]
    assert c["sessionId"] is None and c["kind"] == "picker"
    assert c["asked"] == "" and c["said"] == ""
    assert c["question"] == "무엇을 할까요?"        # 피커는 이미 내용이 있다


def test_isMeta_주입턴은_건너뛴다(transcripts):
    """★ 실측으로 잡은 구멍 — 스킬 본문·캐비앳·붙인 이미지 경로가 user 턴에 끼어 들어온다.

    이걸 안 거르면 카드의 asked 가 "Base directory for this skill: …" 이 되어
    "무엇을 시켰나"가 또 안 보인다(hotel·frogterminal 카드에서 실제로 그렇게 나왔다).
    """
    transcripts("/Users/x/proj", "s1", [
        line("user", "쇼츠 만들어줘"),
        line("user", "Base directory for this skill: /Users/x/.claude/skills/marketing-canon",
             isMeta=True),
        line("user", "[Image: source: /Users/x/.claude/image-cache/1.png]", isMeta=True),
    ])
    assert decisions.context_for(card())["asked"] == "쇼츠 만들어줘"


def test_사용자가_붙인_이미지_질문은_살린다(transcripts):
    """isMeta 가 아닌 '[Image #1] …' 는 사용자가 이미지와 함께 친 진짜 질문이다."""
    transcripts("/Users/x/proj", "s1", [
        line("user", [{"type": "text", "text": "[Image #1] 둘중에 무슨 키로 연결되어 있지?"},
                      {"type": "image", "source": {"type": "base64", "data": "..."}}]),
    ])
    assert decisions.context_for(card())["asked"] == "[Image #1] 둘중에 무슨 키로 연결되어 있지?"
