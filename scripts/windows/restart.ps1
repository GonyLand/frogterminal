# 브릿지를 다시 띄운다(코드를 고친 뒤). 감시 실행기가 5초 안에 새 프로세스를 올린다.
# 주의: 브릿지가 끝나면 그 안의 터미널 세션(클로드·코덱스)도 함께 끝난다 — 지금 일하는 세션이 없을 때만.
#   pwsh -File scripts\windows\restart.ps1          재시작
#   pwsh -File scripts\windows\restart.ps1 -Stop    감시 실행기까지 내린다
param([switch]$Stop)
$ErrorActionPreference = 'Stop'
if ($Stop) { Stop-ScheduledTask -TaskName 'FrogTerminal Bridge' -ErrorAction SilentlyContinue }
$conn = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 7788 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conn) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
if ($Stop) { Write-Output '내렸다'; return }
Start-Sleep -Seconds 7
$up = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 7788 -State Listen -ErrorAction SilentlyContinue
if ($up) { Write-Output "다시 떴다 (pid $($up[0].OwningProcess))" } else { Write-Output '아직 안 떴다 — ~/.frogterminal/bridge.log 를 본다' }
