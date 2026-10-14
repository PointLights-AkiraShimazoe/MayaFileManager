# MayaFileManager - test watcher (r146)
#
# 目的:
#   Claude 側から Windows の回帰スイートを «人手なしで» 走らせるための常駐役。
#   Claude が動かせるのは Linux VM 側だけで、Windows 上で mayapy を起動できない。
#   そこで «合図のファイルが置かれたら run_tests.bat を回す» 係をここに置く。
#
# 使い方:
#   1) これを 1 回起動しておく（PowerShell で右クリック → PowerShell で実行、でも可）
#        powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Claude\PLs-Tools\MayaFileManager\tools\mfm_test_watcher.ps1"
#   2) あとは放置。Claude が .run_tests_request を置くと自動で走り、
#      mfm_tests.log を更新して .run_tests_done を書く。
#   3) 止める時はこのウィンドウで Ctrl+C。
#
# 合図のファイル（リポジトリ直下）:
#   .run_tests_request  … Claude が置く（走らせて、の合図）
#   .run_tests_running  … 実行中の目印
#   .run_tests_done     … 終了時刻と結果の 1 行

$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent $PSScriptRoot
$request = Join-Path $root ".run_tests_request"
$running = Join-Path $root ".run_tests_running"
$done    = Join-Path $root ".run_tests_done"
$bat     = Join-Path $root "run_tests.bat"
$log     = Join-Path $root "mfm_tests.log"

Write-Host "MayaFileManager test watcher"
Write-Host ("  repo : " + $root)
Write-Host ("  watch: " + $request)
Write-Host "  stop : Ctrl+C"
Write-Host ""

if (Test-Path $request) { Remove-Item $request -Force }

while ($true) {
    if (Test-Path $request) {
        # r146: 合図のファイルに語を書いておくと、その語を含むテストだけ回す。
        #   例: echo "paths_symlink preset_dirs" > .run_tests_request
        #   結果は mfm_tests_sel.log に出る（全体の mfm_tests.log は壊さない）。
        $sel = ""
        try { $sel = ((Get-Content $request -Raw -ErrorAction SilentlyContinue) -replace "[\r\n]", " ").Trim() } catch { }
        try { Remove-Item $request -Force } catch { }
        $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Host ("[" + $stamp + "] run_tests.bat start")
        Set-Content -Path $running -Value $stamp -Encoding UTF8
        if (Test-Path $done) { Remove-Item $done -Force }
        try {
            # r146: run_tests.bat の末尾の pause で «キー入力待ち» のまま
            # 止まらないようにする。環境変数で pause を飛ばし、さらに
            # 標準入力を NUL にして二重に塞ぐ。
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
    Start-Sleep -Milliseconds 1000
}
