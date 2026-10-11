# frogterminal 브릿지 감시 실행기(윈도우). 작업 스케줄러가 로그온 때 이 파일을 숨김 창으로 띄운다.
# 브릿지가 죽으면 5초 뒤 다시 띄운다. 로그는 ~/.frogterminal/bridge.log (5MB 넘으면 .old 로 한 번 접는다).
# 손으로 한 번만 돌리고 싶으면: pwsh -File scripts\windows\run-bridge.ps1  (창을 닫으면 브릿지도 끝난다)
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$python = Join-Path $repo '.venv\Scripts\python.exe'
$appDir = Join-Path $env:USERPROFILE '.frogterminal'
$log = Join-Path $appDir 'bridge.log'
New-Item -ItemType Directory -Force $appDir | Out-Null
if (-not (Test-Path $python)) { throw "가상환경이 없다: $python — README.windows.md 의 설치 순서를 먼저 따른다" }

# 감시 실행기가 둘 뜨면 포트 싸움이 난다 — 이름 있는 뮤텍스로 하나만.
$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\frogterminal-bridge-supervisor', [ref]$created)
if (-not $created) { Write-Output 'already running'; exit 0 }

$env:PYTHONUTF8 = '1'
Set-Location $repo
while ($true) {
    if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5MB)) { Move-Item -Force $log "$log.old" }
    "[$(Get-Date -Format 's')] bridge start" | Out-File -Append -Encoding utf8 $log
    $p = Start-Process -FilePath $python -ArgumentList @('-X', 'utf8', '-m', 'bridge.server') `
        -WorkingDirectory $repo -WindowStyle Hidden -PassThru -Wait `
        -RedirectStandardError (Join-Path $appDir 'bridge.err.log') `
        -RedirectStandardOutput (Join-Path $appDir 'bridge.out.log')
    foreach ($f in 'bridge.out.log', 'bridge.err.log') {
        $path = Join-Path $appDir $f
        if (Test-Path $path) { Get-Content $path -Tail 200 | Out-File -Append -Encoding utf8 $log; Remove-Item $path -Force }
    }
    "[$(Get-Date -Format 's')] bridge exit code=$($p.ExitCode) — 5초 뒤 재시작" | Out-File -Append -Encoding utf8 $log
    Start-Sleep -Seconds 5
}
