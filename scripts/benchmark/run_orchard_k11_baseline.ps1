<#
.SYNOPSIS
    Runs the upstream Orchard k=11 single-Action prover benchmark and archives
    the raw Criterion output plus the environment metadata for that run.

.DESCRIPTION
    Stage A baseline runner. It does NOT modify the upstream source tree.
    For every invocation it writes into:

        results/raw/baseline/<RunId>/
            metadata.md          environment + source revision for this run
            bench.log            full stdout/stderr of the Criterion run
            criterion/...        copy of target/criterion for this bench target

.EXAMPLE
    pwsh -File scripts/benchmark/run_orchard_k11_baseline.ps1 -RunId run_001
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunId,

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

$outDir = Join-Path $Engineering "results\raw\baseline\$RunId"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# ---------------------------------------------------------------- preflight
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

    # ------------------------------------------------------------ benchmark
    $env:RAYON_NUM_THREADS = "$RayonThreads"
    $env:CARGO_TERM_COLOR = "never"

    $startedAt = (Get-Date).ToString("o")
    $benchLog = Join-Path $outDir "bench.log"

    $metadata = @"
# Baseline run: $RunId

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
| RAYON_NUM_THREADS | $RayonThreads |

## Command
``````
cargo +$Toolchain bench --locked -p orchard --features circuit --bench orchard_k11_prover
``````

Started: $startedAt
"@

    Set-Content -Path (Join-Path $outDir "metadata.md") -Value $metadata -Encoding utf8

    Write-Host "==> $RunId : $commit (RAYON_NUM_THREADS=$RayonThreads)"

    # Cargo writes progress ("Finished `bench` profile ...") to stderr. Under
    # Windows PowerShell 5.1 a native command's stderr line becomes a
    # NativeCommandError record, which $ErrorActionPreference = "Stop" turns
    # into a terminating error. Redirect through cmd.exe instead so the run
    # streams to disk untouched and $LASTEXITCODE stays meaningful.
    $benchCommand = 'cargo +{0} bench --locked -p orchard --features circuit --bench orchard_k11_prover > "{1}" 2>&1' -f `
        $Toolchain, $benchLog
    & cmd.exe /c $benchCommand
    $exit = $LASTEXITCODE

    if (Test-Path $benchLog) {
        Get-Content $benchLog | Select-Object -Last 30
    }

    $finishedAt = (Get-Date).ToString("o")
    Add-Content -Path (Join-Path $outDir "metadata.md") -Value "`nFinished: $finishedAt`nExit code: $exit"

    # ------------------------------------------------------ archive criterion
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
