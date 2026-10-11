// 화면 쪽 상수. 실제 API·WS 는 같은 출처(상대경로)로 부르므로 브릿지 주소를 여기 적을 필요가 없다.
// BRIDGE 는 별도 "문패" 도메인(정적 호스팅의 리다이렉트 페이지)을 둘 때만 채운다 — 비워 두면 리다이렉트 없음.
window.FROG = {
  BRIDGE: "",                                  // 예: "my-mac.tailnet-name.ts.net"
  NAMEPLATES: [],                              // 이 호스트로 열리면 BRIDGE 로 보낸다(*.vercel.app 은 항상)
  MAX_UPLOAD_BYTES: 50 * 1024 * 1024,
};
