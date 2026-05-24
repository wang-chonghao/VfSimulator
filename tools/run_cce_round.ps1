param(
    [Parameter(Mandatory = $true)] [string] $DslPath,
    [Parameter(Mandatory = $true)] [string] $RoundTag,
    [string] $KernelName = "foo_add",
    [int] $NumInputs = 2,
    [int] $NumOutputs = 1,
    [int] $TotalElems = 1024,
    [string] $WslDistro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Invoke-Wsl {
    param([Parameter(Mandatory = $true)] [string] $Cmd)
    $tmpDir = Join-Path $repoRoot "results\cce_rounds"
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
    $tmpShWin = Join-Path $tmpDir ("_wsl_cmd_" + [System.Guid]::NewGuid().ToString("N") + ".sh")
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmpShWin, "#!/usr/bin/env bash`nset -euo pipefail`n$Cmd`n", $utf8NoBom)
    $tmpShWsl = $tmpShWin.Replace("\", "/").Replace("D:", "/mnt/d")
    $wslCall = "wsl -d $WslDistro -- bash -lc ""bash '$tmpShWsl'"""
    $out = & cmd /c $wslCall 2>&1
    Remove-Item -LiteralPath $tmpShWin -Force -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed ($LASTEXITCODE): $Cmd`n$out"
    }
    return ($out -join "`n")
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dslAbs = (Resolve-Path $DslPath).Path
$dslWsl = $dslAbs.Replace("\", "/").Replace("D:", "/mnt/d")

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stem = "GeLU_poly_${RoundTag}_$stamp"

$buildDirWin = Join-Path $repoRoot "ascend_runner\build\${stem}_native_simexec"
$simexecWin = Join-Path $buildDirWin "${stem}_simexec"
$mixObjWin = Join-Path $buildDirWin "${stem}_mix.o"

$msprofDir = "/home/lenovo/msprof_run/${stem}_native_simexec"
$popPath = "${msprofDir}/core0.veccore0.instr_popped_log.dump"
$donePath = "${msprofDir}/core0.veccore0.instr_log.dump"

$resultDir = Join-Path $repoRoot "results\cce_rounds"
New-Item -ItemType Directory -Force -Path $resultDir | Out-Null
$buildLog = Join-Path $resultDir "${stem}_build.log"
$runLog = Join-Path $resultDir "${stem}_run.log"
$resultJson = Join-Path $resultDir "${stem}.json"

Write-Host "[round] stem=$stem"
Write-Host "[round] dsl=$dslAbs"

# 1) Build with fixed fairness flags.
$buildCmd = @"
set -euo pipefail
cd /mnt/d/VfSimulator
CCEC_EXTRA_FLAGS="-mllvm -cce-aicore-vec-misched=0" bash ascend_runner/current/build_native_simexec.sh "$dslWsl" "$stem"
ls -ld "/mnt/d/VfSimulator/ascend_runner/build/${stem}_native_simexec"
"@
$buildOut = Invoke-Wsl -Cmd $buildCmd
$buildOut | Set-Content -Path $buildLog -Encoding UTF8

if (!(Test-Path $buildDirWin)) { throw "Build dir missing: $buildDirWin" }
if (!(Test-Path $simexecWin)) { throw "Simexec missing: $simexecWin" }
if (!(Test-Path $mixObjWin)) { throw "Mix object missing: $mixObjWin" }

# 2) Run simulator.
$simexecWsl = $simexecWin.Replace("\", "/").Replace("D:", "/mnt/d")
$mixObjWsl = $mixObjWin.Replace("\", "/").Replace("D:", "/mnt/d")
$runCmd = @"
set -euo pipefail
cd /mnt/d/VfSimulator
bash ascend_runner/current/run_native_simexec.sh "$simexecWsl" "$mixObjWsl" "$KernelName" "$NumInputs" "$NumOutputs" "$TotalElems"
"@
$runOut = Invoke-Wsl -Cmd $runCmd
$runOut | Set-Content -Path $runLog -Encoding UTF8

# 3) Validate msprof output exists and parse vf timing.
$checkCmd = @"
set -euo pipefail
test -d "$msprofDir"
test -f "$popPath"
test -f "$donePath"
grep -ni "vf" "$popPath" | tail -n 1
echo "----"
grep -ni "vf" "$donePath" | tail -n 1
"@
$vfLines = Invoke-Wsl -Cmd $checkCmd
$parts = $vfLines -split "----"
if ($parts.Count -lt 2) { throw "Failed to parse VF lines.`n$vfLines" }
$popLine = $parts[0].Trim()
$doneLine = $parts[1].Trim()

$startMatch = [regex]::Match($popLine, "\[0*([0-9]+)\]")
$endMatch = [regex]::Match($doneLine, "\[0*([0-9]+)\]")
if (!$startMatch.Success -or !$endMatch.Success) {
    throw "Failed to parse start/end cycles.`n$vfLines"
}
$vfStart = [int]$startMatch.Groups[1].Value
$vfEnd = [int]$endMatch.Groups[1].Value
$vfLatency = $vfEnd - $vfStart
$checkPass = $runOut -match "\[CHECK\]\s+PASS"

$result = [ordered]@{
    round_tag = $RoundTag
    stem = $stem
    dsl = $dslAbs
    kernel = $KernelName
    check_pass = $checkPass
    vf_start_time = $vfStart
    vf_end_time = $vfEnd
    vf_latency = $vfLatency
    msprof_dir = $msprofDir
    build_log = $buildLog
    run_log = $runLog
}

($result | ConvertTo-Json -Depth 4) | Set-Content -Path $resultJson -Encoding UTF8
Write-Host "[ok] result=$resultJson"
Write-Host "[ok] vf_start=$vfStart vf_end=$vfEnd vf_latency=$vfLatency check_pass=$checkPass"
