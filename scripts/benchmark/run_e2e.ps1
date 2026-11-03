# Run the Orchard end-to-end harness with a fixed CPU affinity mask.
#
# Why affinity is mandatory: on this i7-14700 (8 P-cores + 12 E-cores) an
# unpinned background process is scheduled onto E-cores and runs ~2.07x slower.
# See results/analysis/baseline_summary.md. Any thread-scaling run that changes
# only RAYON_NUM_THREADS and not the mask measures Windows scheduling policy,
# not the algorithm.
#
# The mask is applied by the harness itself (SetProcessAffinityMask) rather than
# by the launcher: PowerShell 5.1's Start-Process throws on this box because both
# HTTP_PROXY and http_proxy are set, and a launcher-side mask would also pin the
# launcher's own threads, which we do not want.
#
# Usage:
#   run_e2e.ps1 -RunId e2e_1s2o_t1 -Threads 1 -Mask 0x1 -Version v2 -Spends 1 -Outputs 2

param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [string]$Mode = "v2",
    [int]$Threads = 1,
    [string]$Mask = "0x1",
    [string]$Version = "v2",
    [int]$Spends = 1,
    [int]$Outputs = 2,
    [int]$Reps = 3,
    [int]$Seed = 7,
    [string]$Bin = "D:\Research\zcash-orchard-engineering\experiments\transaction_e2e\harness\target\release\orchard-e2e.exe",
    [string]$OutDir = "D:\Research\zcash-orchard-engineering\results\transaction_e2e\raw"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (-not (Test-Path $Bin)) { throw "harness binary not found: $Bin (run cargo build --release first)" }

$log = Join-Path $OutDir "$RunId.log"

# cmd handles the redirection: PowerShell 5.1 turns native stderr into a
# terminating error, and cargo/the harness write progress there.
$inner = 'set "RAYON_NUM_THREADS={0}" && "{1}" --mode {2} --version {3} --spends {4} --outputs {5} --reps {6} --seed {7} --mask {8} --tag {9} > "{10}" 2>&1' -f `
    $Threads, $Bin, $Mode, $Version, $Spends, $Outputs, $Reps, $Seed, $Mask, $RunId, $log

$t0 = Get-Date
& cmd.exe /c $inner
$exit = $LASTEXITCODE
$wall = ((Get-Date) - $t0).TotalSeconds

if ($exit -ne 0) {
    Write-Host "FAILED exit=$exit"
    if (Test-Path $log) { Get-Content $log -Tail 25 | ForEach-Object { Write-Host "  $_" } }
    exit 1
}

Write-Host "==> $RunId  threads=$Threads mask=$Mask wall=$([math]::Round($wall,2))s"
Get-Content $log | Where-Object { $_ -match '^(STAGE|BUNDLE|RESULT)' } | ForEach-Object { Write-Host "  $_" }
"WALL_S=$wall" | Add-Content -Path $log
