param(
    [Parameter(Mandatory = $true)] [string] $Trace,
    [Parameter(Mandatory = $true)] [string] $SourceDsl,
    [Parameter(Mandatory = $true)] [int] $TripCount,
    [string] $KernelName = 'foo_add',
    [int] $NumInputs = 2,
    [int] $NumOutputs = 1,
    [int] $TotalElems = 1024,
    [string] $OooModel = 'consumer-done',
    [ValidateSet('off','on')] [string] $CutPenalty = 'off',
    [double] $CutPenaltyScale = 1.0,
    [string] $ResultStem = '',
    [switch] $SkipOptimize,
    [switch] $SkipDag,
    [switch] $SkipDsl
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $ResultStem) {
    $ResultStem = [System.IO.Path]::GetFileNameWithoutExtension($Trace) + "_I$TripCount"
}

$traceAbs = Resolve-Path $Trace
$sourceDslAbs = Resolve-Path $SourceDsl
$tempTrace = Join-Path $root "results\${ResultStem}_baseline_trace.json"
$baselineOut = Join-Path $root "results\${ResultStem}_baseline"
$theoreticalOut = Join-Path $root "results\${ResultStem}_theoretical"
$optJson = Join-Path $root "results\${ResultStem}_optimized.json"
$optMeta = Join-Path $root "results\${ResultStem}_optimized_meta.json"
$optDag = Join-Path $root "results\${ResultStem}_optimized_dag.png"
$optDsl = Join-Path $root "cce_code\consumer_done\${ResultStem}_optimized.dsl"

Write-Host "[workflow] preparing trip-count-adjusted baseline trace"
$j = Get-Content $traceAbs -Raw | ConvertFrom-Json
$j.params.I = $TripCount
$j | ConvertTo-Json -Depth 100 | Set-Content $tempTrace

Write-Host "[workflow] baseline model run"
python "$root\main.py" --trace $tempTrace --out_dir $baselineOut

Write-Host "[workflow] theoretical-limit run"
python "$root\main.py" --trace $tempTrace --out_dir $theoreticalOut --theoretical-limit

if (-not $SkipOptimize) {
    Write-Host "[workflow] optimization run"
    python "$root\optimizer\generic_heuristic_split_optimizer.py" $traceAbs --trip-count $TripCount --ooo-model $OooModel --cut-penalty $CutPenalty --cut-penalty-scale $CutPenaltyScale --output $optJson --meta-out $optMeta

    if (-not $SkipDag) {
        Write-Host "[workflow] generating DAG"
        $dagPy = 'D:\miniconda3\envs\vfsim\python.exe'
        if (-not (Test-Path $dagPy)) { $dagPy = 'python' }
        & $dagPy "$root\tools\visualize_dag.py" $optJson --output $optDag
    }

    if (-not $SkipDsl) {
        Write-Host "[workflow] generating DSL"
        python "$root\tools\generate_gelu_poly_split_dsl.py" $optJson $optDsl --simd-name (([System.IO.Path]::GetFileNameWithoutExtension($optDsl)).ToLower() + '_simd_ub')
    }
}

Write-Host ''
Write-Host '[workflow] baseline CCE simulator commands:'
Write-Host "bash ascend_runner/build_native_simexec.sh $($sourceDslAbs.Path) ${ResultStem}_baseline"
Write-Host "bash ascend_runner/run_native_simexec.sh /mnt/d/VfSimulator/ascend_runner/build/${ResultStem}_baseline_native_simexec/${ResultStem}_baseline_simexec /mnt/d/VfSimulator/ascend_runner/build/${ResultStem}_baseline_native_simexec/${ResultStem}_baseline_mix.o $KernelName $NumInputs $NumOutputs $TotalElems"

if (-not $SkipOptimize -and -not $SkipDsl) {
    $optDslWsl = $optDsl.Replace('D:\VfSimulator', '/mnt/d/VfSimulator').Replace('\','/')
    Write-Host ''
    Write-Host '[workflow] optimized CCE simulator commands:'
    Write-Host "bash ascend_runner/build_native_simexec.sh $optDslWsl ${ResultStem}_optimized"
    Write-Host "bash ascend_runner/run_native_simexec.sh /mnt/d/VfSimulator/ascend_runner/build/${ResultStem}_optimized_native_simexec/${ResultStem}_optimized_simexec /mnt/d/VfSimulator/ascend_runner/build/${ResultStem}_optimized_native_simexec/${ResultStem}_optimized_mix.o $KernelName $NumInputs $NumOutputs $TotalElems"
}

Write-Host ''
Write-Host '[workflow] outputs:'
Write-Host "  baseline trace : $tempTrace"
Write-Host "  baseline logs  : $baselineOut"
Write-Host "  theoretical    : $theoreticalOut"
if (-not $SkipOptimize) {
    Write-Host "  optimized json : $optJson"
    Write-Host "  optimized meta : $optMeta"
    if (-not $SkipDag) { Write-Host "  optimized dag  : $optDag" }
    if (-not $SkipDsl) { Write-Host "  optimized dsl  : $optDsl" }
}
