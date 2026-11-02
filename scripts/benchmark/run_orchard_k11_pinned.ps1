<#
.SYNOPSIS
    Runs the upstream Orchard k=11 single-Action prover benchmark pinned to a
    fixed logical CPU, so that hybrid-architecture (P-core / E-core) scheduling
    noise cannot be mistaken for a change in proving cost.

.DESCRIPTION
    Stage A / Stage D helper. Same fixture and upstream sources as
    run_orchard_k11_baseline.ps1, but the benchmark process tree is started with
    a fixed processor affinity mask ("start /affinity <hex>"). Child processes
    inherit the mask, so cargo and the benchmark binary stay on that CPU.

    Output goes to results/raw/<Suite>/<RunId>/ with the same layout as the
    unpinned runner:

        metadata.md          environment + affinity mask + source revision
        bench.log            full stdout/stderr of the Criterion run
        criterion/...        copy of target/criterion for this bench target

.EXAMPLE
    # logical CPU 0 (P-core 0, first SMT thread)
    .\run_orchard_k11_pinned.ps1 -RunId p0_run_001 -AffinityMask 0x1 -Suite affinity

.EXAMPLE
    # logical CPU 16 (first E-core on i7-14700)
    .\run_orchard_k11_pinned.ps1 -RunId e0_run_001 -AffinityMask 0x10000 -Suite affinity
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunId,

    [Parameter(Mandatory = $true)]
    [string]$AffinityMask,

    [string]$Suite = "affinity",

    [int]$RayonThreads = 1,

    [string]$Upstream = "D:\Research\orchard-upstream",

    [string]$Engineering = "D:\Research\zcash-orchard-engineering",

    [string]$Toolchain = "1.88",

    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"

function Invoke-Capture {
    param([string]$File, [string[]]$Arguments)
    $output = & $File @Arguments 2>&1
    return ($output | Out-String).Trim()
}

$outDir = Join-Path $Engineering "results\raw\$Suite\$RunId"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$benchLog = Join-Path $outDir "bench.log"

Push-Location $Upstream
try {
    $commit = Invoke-Capture git @("rev-parse", "HEAD")
    $describe = Invoke-Capture git @("describe", "--tags", "--always")
    $dirty = Invoke-Capture git @("status", "--porcelain=v1")

    if (-not $SkipPreflight -and $dirty) {
        throw "Upstream working tree is dirty; a baseline run must use pristine sources.`n$dirty"
    }

    $lockHash = (Get-FileHash -Algorithm SHA256 "Cargo.lock").Hash
    $rustcVersion = Invoke-Capture "rustc" @("+$Toolchain", "--version")
    $cargoVersion = Invoke-Capture "cargo" @("+$Toolchain", "--version")

    $os = Get-CimInstance Win32_OperatingSystem
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $scheme = (powercfg /getactivescheme) -join ""

    $env:RAYON_NUM_THREADS = "$RayonThreads"
    $env:CARGO_TERM_COLOR = "never"

    $startedAt = (Get-Date).ToString("o")

    $metadata = @"
# Pinned run: $RunId

## Source
| Field | Value |
| --- | --- |
| Repository | `$Upstream` |
| Commit | ``$commit`` |
| Describe | ``$describe`` |
| Working tree | $(if ($dirty) { 'dirty' } else { 'clean' }) |
| Cargo.lock SHA256 | ``$lockHash`` |

## Toolchain
| Field | Value |
| --- | --- |
| rustc | ``$rustcVersion`` |
| cargo | ``$cargoVersion`` |
| Toolchain override | ``$Toolchain`` (from rust-toolchain.toml) |

## Machine
| Field | Value |
| --- | --- |
| OS | $($os.Caption) (build $($os.BuildNumber)) |
| CPU | $($cpu.Name) |
| Cores / logical | $($cpu.NumberOfCores) / $($cpu.NumberOfLogicalProcessors) |
| Memory | $([math]::Round($os.TotalVisibleMemorySize / 1MB, 2)) GiB |
| Power scheme | $scheme |
| RAYON_NUM_THREADS | $RayonThreads |
| Processor affinity mask | ``$AffinityMask`` |

## Command
``````
start /affinity $AffinityMask cargo +$Toolchain bench --locked -p orchard --features circuit --bench orchard_k11_prover
``````

Started: $startedAt
"@

    Set-Content -Path (Join-Path $outDir "metadata.md") -Value $metadata -Encoding utf8

    Write-Host "==> $RunId : $commit (affinity=$AffinityMask, RAYON_NUM_THREADS=$RayonThreads)"

    $inner = 'cargo +{0} bench --locked -p orchard --features circuit --bench orchard_k11_prover > {1} 2>&1' -f `
        $Toolchain, $benchLog
    & cmd.exe /c ('start /b /wait /affinity {0} cmd.exe /c "{1}"' -f $AffinityMask, $inner)
    $exit = $LASTEXITCODE

    $finishedAt = (Get-Date).ToString("o")
    Add-Content -Path (Join-Path $outDir "metadata.md") -Value "`nFinished: $finishedAt`nExit code: $exit"

    if (Test-Path $benchLog) {
        Get-Content $benchLog | Select-Object -Last 20
    }

    $criterionSrc = Join-Path $Upstream "target\criterion\orchard-k11"
    if (Test-Path $criterionSrc) {
        $criterionDst = Join-Path $outDir "criterion"
        New-Item -ItemType Directory -Force -Path $criterionDst | Out-Null
        Copy-Item -Recurse -Force $criterionSrc (Join-Path $criterionDst "orchard-k11")
    }

    if ($exit -ne 0) {
        throw "Benchmark exited with code $exit; see $benchLog"
    }
}
finally {
    Pop-Location
}
