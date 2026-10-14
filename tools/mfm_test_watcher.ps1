# MayaFileManager - watcher (r146)
#
# 目的:
#   Claude 側から «Windows でしか出来ないこと» を人手なしで走らせるための常駐役。
#   Claude が動かせるのは Linux VM 側だけで、Windows 上で mayapy を起動したり
#   GitHub へ push したり出来ない（認証情報は Windows にしか無い）。
#   そこで «合図のファイルが置かれたら、この係が Windows 側で実行する»。
#   認証情報はこの PC から一歩も出ない。
#
# 使い方:
#   1) これを 1 回起動しておく（tools\run_test_watcher.bat をダブルクリックでも可）
#        powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Claude\PLs-Tools\MayaFileManager\tools\mfm_test_watcher.ps1"
#   2) あとは放置。止める時はこのウィンドウで Ctrl+C。
#   3) **この .ps1 を書き換えると自動で読み込み直す。** 再起動は要らない。
#
# 合図のファイル（リポジトリ直下。すべて .gitignore 済み）:
#   .run_tests_request  … 回帰スイートを回す（中身に語を書くとその語を含むテストだけ）
#   .run_tests_running  … 実行中の目印
#   .run_tests_done     … 終了時刻と結果の 1 行
#   .run_git_request    … git の引数を 1 行で書く（例: push origin main）
#   .run_git_out.log    … その出力
#   .run_git_done       … 終了時刻と終了コード
#
# git は «読む» と «上げる» だけ許す。履歴を壊す操作（--force / --mirror 等）は
# この係が拒否する。取り返しのつかない操作を自動化しないため。

$ErrorActionPreference = "Stop"
$self    = $PSCommandPath
$root    = Split-Path -Parent $PSScriptRoot
$request = Join-Path $root ".run_tests_request"
$running = Join-Path $root ".run_tests_running"
$done    = Join-Path $root ".run_tests_done"
$bat     = Join-Path $root "run_tests.bat"
$gitReq  = Join-Path $root ".run_git_request"
$gitOut  = Join-Path $root ".run_git_out.log"
$gitDone = Join-Path $root ".run_git_done"

$selfStamp = (Get-Item $self).LastWriteTimeUtc

$AllowedVerbs = @("push", "fetch", "pull", "ls-remote", "tag", "status", "log",
                  "rev-parse", "describe")
$BannedWords  = @("--force", "-f", "--force-with-lease", "--mirror", "--prune",
                  "reset", "clean", "filter-repo", "filter-branch", "gc",
                  "reflog", "update-ref")

function Invoke-GitRequest($line) {
    $gargs = @($line -split '\s+' | Where-Object { $_ -ne "" })
    if ($gargs.Count -eq 0) { return @{ out = "empty request"; code = 1 } }
    if ($AllowedVerbs -notcontains $gargs[0]) {
        return @{ out = ("refused (verb not allowed): " + $gargs[0]); code = 1 }
    }
    foreach ($w in $BannedWords) {
        if ($gargs -contains $w) {
            return @{ out = ("refused (banned word): " + $w); code = 1 }
        }
    }
    # git の出力は «必ず» 拾う。
    # 注意: $ErrorActionPreference="Stop" のまま «& git ... 2>&1» をすると、
    # git が stderr に 1 行書いただけで終了エラー扱いになり、肝心の
    # エラー本文が取れない（実機 2026-10-14: push の失敗理由が出なかった）。
    # 出力はファイルへ落として読む。
    $tmpO = Join-Path $env:TEMP ("mfm_git_o_" + [guid]::NewGuid().ToString("N") + ".txt")
    $tmpE = Join-Path $env:TEMP ("mfm_git_e_" + [guid]::NewGuid().ToString("N") + ".txt")
    $code = 1
    $out = ""
    $prevEA = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $proc = Start-Process -FilePath "git" -ArgumentList $gargs `
                    -WorkingDirectory $root -NoNewWindow -Wait -PassThru `
                    -RedirectStandardOutput $tmpO -RedirectStandardError $tmpE
        $code = $proc.ExitCode
        if (Test-Path $tmpO) { $out += (Get-Content $tmpO -Raw -ErrorAction SilentlyContinue) }
        if (Test-Path $tmpE) { $out += (Get-Content $tmpE -Raw -ErrorAction SilentlyContinue) }
    } catch {
        $out = $_.Exception.Message
        $code = 1
    } finally {
        $ErrorActionPreference = $prevEA
        if (Test-Path $tmpO) { Remove-Item $tmpO -Force -ErrorAction SilentlyContinue }
        if (Test-Path $tmpE) { Remove-Item $tmpE -Force -ErrorAction SilentlyContinue }
    }
    return @{ out = $out; code = $code }
}

Write-Host "MayaFileManager watcher"
Write-Host ("  repo : " + $root)
Write-Host ("  watch: " + $request)
Write-Host ("       : " + $gitReq)
Write-Host "  stop : Ctrl+C"
Write-Host ""

if (Test-Path $request) { Remove-Item $request -Force }
if (Test-Path $gitReq)  { Remove-Item $gitReq -Force }

while ($true) {

    # ── 自分が書き換わっていたら読み込み直す（再起動の手間をなくす）──
    if ((Get-Item $self).LastWriteTimeUtc -ne $selfStamp) {
        Start-Sleep -Milliseconds 500      # 書き込み途中を読まないため
        Write-Host ""
        Write-Host ("[" + (Get-Date -Format "HH:mm:ss") + "] watcher が更新されたので読み込み直します")
        Write-Host ""
        & $self
        exit
    }

    # ── 回帰スイート ──────────────────────────────────────────────
    if (Test-Path $request) {
        $sel = ""
        try { $sel = ((Get-Content $request -Raw -ErrorAction SilentlyContinue) -replace "[\r\n]", " ").Trim() } catch { }
        try { Remove-Item $request -Force } catch { }
        $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Host ("[" + $stamp + "] run_tests.bat start")
        Set-Content -Path $running -Value $stamp -Encoding UTF8
        if (Test-Path $done) { Remove-Item $done -Force }
        try {
            # run_tests.bat の末尾の pause で止まらないよう、環境変数で飛ばし、
            # さらに標準入力を NUL にして二重に塞ぐ。
            $env:MFM_NO_PAUSE = "1"
            if ($sel -ne "") {
                Write-Host ("  only: " + $sel)
                & cmd.exe /c "`"$bat`" $sel < NUL" | Out-Null
            } else {
                & cmd.exe /c "`"$bat`" < NUL" | Out-Null
            }
        } catch {
            Write-Host ("  error: " + $_.Exception.Message)
        }
        $tail = ""
        $log = Join-Path $root "mfm_tests.log"
        if ($sel -ne "") { $log = Join-Path $root "mfm_tests_sel.log" }
        if (Test-Path $log) {
            $lines = Get-Content $log -Encoding UTF8
            if ($lines.Count -gt 0) { $tail = $lines[$lines.Count - 1] }
        }
        $end = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Set-Content -Path $done -Value ($end + " | " + $tail) -Encoding UTF8
        if (Test-Path $running) { Remove-Item $running -Force }
        Write-Host ("[" + $end + "] done: " + $tail)
    }

    # ── git（認証情報はこの PC から出さない）──────────────────────
    if (Test-Path $gitReq) {
        $line = ""
        try { $line = ((Get-Content $gitReq -Raw -ErrorAction SilentlyContinue) -replace "[\r\n]", " ").Trim() } catch { }
        try { Remove-Item $gitReq -Force } catch { }
        if (Test-Path $gitDone) { Remove-Item $gitDone -Force }
        $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Host ("[" + $stamp + "] git " + $line)
        $r = Invoke-GitRequest $line
        Set-Content -Path $gitOut -Value ("$ git " + $line + "`r`n" + $r.out) -Encoding UTF8
        $end = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Set-Content -Path $gitDone -Value ($end + " | exit=" + $r.code) -Encoding UTF8
        Write-Host ("[" + $end + "] git done: exit=" + $r.code)
    }

    Start-Sleep -Milliseconds 1000
}
