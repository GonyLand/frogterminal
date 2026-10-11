# Security Policy / 보안 정책

## 신고 (Reporting)

취약점을 찾으면 공개 이슈 대신 GitHub 의 **Private vulnerability reporting**(Security 탭 → Report a vulnerability) 으로 보내 주세요. 재현 절차와 영향을 적어 주세요. 개인 시간에 유지하는 프로젝트라 응답 시한은 약속하지 않습니다.

Please report vulnerabilities privately via GitHub's "Report a vulnerability" (Security tab) rather than a public issue. Include reproduction steps. This project is maintained in spare time, so no response time is guaranteed.

## 위협 모델 (Threat model)

frogterminal 은 **운영자 본인 계정 권한의 원격 셸**입니다. 인증을 통과한 요청은 터미널 입력·파일 편집·에이전트 실행을 전부 할 수 있습니다. 따라서 보안의 전부는 "누가 들어오나"입니다.

지키는 것:
- 브릿지는 127.0.0.1 에만 바인드 (설정으로 바꿀 수 없음)
- Tailscale Funnel 로 브릿지 포트가 공개 인터넷에 나가 있으면 기동 거부(판정 불가도 거부)
- 인증 두 겹 — Tailscale 신원 헤더(`allowedLogins` 설정 시) + 토큰 쿠키(항상). 토큰은 `hmac.compare_digest` 로 비교, 실패 10회/10분 잠금
- Origin 검사(허용목록 또는 같은 출처), 쿠키 `SameSite=Lax`·`HttpOnly`
- 파일 API: 프로젝트 루트 밖·비밀파일 이름(`.env*`, `.git*`, 키, `credential`, `secret`, `auth.json`, `*token.json` …) 404, 심링크는 해석 뒤 경계 검사
- 정적 파일 화이트리스트(디렉터리 통째 서빙 없음), 모든 화면 렌더는 `textContent`
- 감사로그(attach·kill·settings·upload·open·filewrite·decision·login)

지키지 않는 것(설계상):
- 다중 사용자 간 격리 — `allowedLogins` 의 모든 계정은 동등한 운영자
- 로컬 공격자 — 같은 맥에서 같은 사용자로 돌아가는 프로세스는 토큰 파일을 읽을 수 있음
- 터미널 안에서 사용자가 실행하는 명령의 결과 — 그것이 이 도구의 목적

## 운영 권고 (Hardening checklist)

1. Tailscale Serve 로만 노출하고 `allowedLogins`·`allowedOrigins` 를 채운다
2. `openAnywhere` 는 필요할 때만 `true`
3. `~/.frogterminal/token` 은 0600 유지, 기기 분실 시 파일을 지우고 재시작(전 기기 재로그인)
4. `hooks/install_hooks.py --dry-run` 으로 전역 Claude 설정 변경을 먼저 확인
5. 업데이트 뒤 `pip-audit -r requirements.txt`
