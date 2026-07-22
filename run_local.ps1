$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path -LiteralPath $envFile) {
  Get-Content -LiteralPath $envFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or $line -notmatch "=") {
      return
    }
    $key, $value = $line -split "=", 2
    $key = $key.Trim().TrimStart([char]0xFEFF)
    $value = $value.Trim()
    if ($key -and $value -and -not [Environment]::GetEnvironmentVariable($key, "Process")) {
      [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
  }
  Write-Host "Loaded local .env into process environment."
}

# Kill existing process on 8015 if any
$lines = netstat -ano | Select-String ":8015"
if ($lines) {
  $pids = @()
  foreach ($ln in $lines) {
    $parts = ($ln.ToString().Trim() -split "\s+") | Where-Object { $_ }
    if ($parts.Length -ge 5 -and $parts[4] -match "^\d+$" -and $parts[4] -ne "0") {
      $pids += [int]$parts[4]
    }
  }
  $pids = $pids | Select-Object -Unique
  foreach ($pid in $pids) {
    try { Stop-Process -Id $pid -Force } catch {}
  }
}

if (-not $env:NCS_MCP_URL) {
  Write-Warning "NCS_MCP_URL is not set. MCP-only interview flow requires a running NCS_MCP endpoint."
}

Write-Host "Starting server on http://127.0.0.1:8015 ..."
python -m uvicorn app.main:app --host 127.0.0.1 --port 8015
