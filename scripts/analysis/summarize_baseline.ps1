<#
.SYNOPSIS
    Collects the Criterion estimates of every archived baseline run into one CSV
    and prints a compact comparison table.

.DESCRIPTION
    Reads results/raw/baseline/run_*/criterion/orchard-k11/prove-one-action/new/estimates.json
    (written by Criterion 0.5) and emits results/analysis/baseline_summary.csv.

    Criterion reports raw durations in nanoseconds.
#>
[CmdletBinding()]
param(
    [string]$Engineering = "D:\Research\zcash-orchard-engineering"
)

$ErrorActionPreference = "Stop"

$baselineRoot = Join-Path $Engineering "results\raw\baseline"
$outCsv = Join-Path $Engineering "results\analysis\baseline_summary.csv"

$rows = foreach ($run in Get-ChildItem $baselineRoot -Directory | Sort-Object Name) {
    $estimates = Join-Path $run.FullName "criterion\orchard-k11\prove-one-action\new\estimates.json"
    if (-not (Test-Path $estimates)) { continue }

    $e = Get-Content $estimates -Raw | ConvertFrom-Json
    $meta = Join-Path $run.FullName "metadata.md"
    $commit = ""
    if (Test-Path $meta) {
        $m = Select-String -Path $meta -Pattern '^\| Commit \| `([0-9a-f]+)`' | Select-Object -First 1
        if ($m) { $commit = $m.Matches[0].Groups[1].Value }
    }

    [pscustomobject]@{
        Run             = $run.Name
        Commit          = $commit
        MeanNs          = [math]::Round($e.mean.point_estimate)
        MeanLowerNs     = [math]::Round($e.mean.confidence_interval.lower_bound)
        MeanUpperNs     = [math]::Round($e.mean.confidence_interval.upper_bound)
        MedianNs        = [math]::Round($e.median.point_estimate)
        StdDevNs        = [math]::Round($e.std_dev.point_estimate)
        MeanMs          = [math]::Round($e.mean.point_estimate / 1e6, 3)
        MedianMs        = [math]::Round($e.median.point_estimate / 1e6, 3)
    }
}

$rows | Format-Table -AutoSize

New-Item -ItemType Directory -Force -Path (Split-Path $outCsv) | Out-Null
$rows | Export-Csv -Path $outCsv -NoTypeInformation -Encoding utf8
Write-Host "Wrote $outCsv"
