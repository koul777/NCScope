$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

function Test-LocalPortOpen {
  param(
    [Parameter(Mandatory=$true)][string]$HostName,
    [Parameter(Mandatory=$true)][int]$Port
  )
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $async = $client.BeginConnect($HostName, $Port, $null, $null)
    $ok = $async.AsyncWaitHandle.WaitOne(500, $false)
    if ($ok) { $client.EndConnect($async) }
    $client.Close()
    return [bool]$ok
  } catch {
    return $false
  }
}

function ConvertTo-PSLiteral {
  param([Parameter(Mandatory=$true)][string]$Value)
  return "'" + ($Value -replace "'", "''") + "'"
}

function Start-LocalNcsMcpIfNeeded {
  $autoStartFlag = [string]$env:NCSCOPE_AUTO_START_NCS_MCP
  if ($autoStartFlag -and $autoStartFlag.Trim().ToLower() -in @("0", "false", "no", "n")) {
    Write-Host "NCS_MCP auto-start disabled by NCSCOPE_AUTO_START_NCS_MCP."
    return $null
  }

  if (-not $env:NCS_MCP_URL) {
    $env:NCS_MCP_URL = "http://127.0.0.1:8778/mcp"
    Write-Host "NCS_MCP_URL not set. Using default $env:NCS_MCP_URL"
  }

  try {
    $endpoint = [Uri]$env:NCS_MCP_URL
  } catch {
    Write-Warning "NCS_MCP_URL is invalid: $env:NCS_MCP_URL"
    return $null
  }

  $hostName = $endpoint.Host
  $port = $endpoint.Port
  if ($port -lt 1) {
    $port = if ($endpoint.Scheme -eq "https") { 443 } else { 80 }
  }

  if ($hostName -notin @("127.0.0.1", "localhost")) {
    Write-Host "NCS_MCP_URL is not local. Skipping local NCS_MCP auto-start: $env:NCS_MCP_URL"
    return $null
  }

  if (Test-LocalPortOpen -HostName $hostName -Port $port) {
    Write-Host "NCS_MCP already reachable at $env:NCS_MCP_URL"
    return $null
  }

  $repoCandidates = @()
  if ($env:NCS_MCP_REPO) { $repoCandidates += $env:NCS_MCP_REPO }
  $repoCandidates += (Join-Path $PSScriptRoot "..\NCS_MCP")
  $repoCandidates += "C:\workspace\NCS_MCP"

  $mcpRepo = $null
  foreach ($candidate in $repoCandidates) {
    if (-not $candidate) { continue }
    $resolved = Resolve-Path -LiteralPath $candidate -ErrorAction SilentlyContinue
    if ($resolved -and (Test-Path -LiteralPath (Join-Path $resolved.Path "src\ncs_mcp\server.py"))) {
      $mcpRepo = $resolved.Path
      break
    }
  }

  if (-not $mcpRepo) {
    Write-Warning "Could not find NCS_MCP repo. Set NCS_MCP_REPO or start NCS_MCP manually."
    return $null
  }

  $dbPath = $env:NCS_DB_PATH
  if (-not $dbPath) {
    $dbPath = Join-Path $mcpRepo "data\processed\ncs.db"
  }
  if (-not (Test-Path -LiteralPath $dbPath)) {
    Write-Warning "NCS DB not found at $dbPath. Set NCS_DB_PATH or prepare the serving DB first."
    return $null
  }

  $pythonExe = Join-Path $mcpRepo ".venv\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = "python"
  }
  $srcPath = Join-Path $mcpRepo "src"
  $bindHost = "127.0.0.1"

  $command = @"
`$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $(ConvertTo-PSLiteral $mcpRepo)
`$env:PYTHONPATH = $(ConvertTo-PSLiteral $srcPath)
`$env:NCS_DB_PATH = $(ConvertTo-PSLiteral $dbPath)
`$env:NCS_MCP_READ_ONLY = '1'
& $(ConvertTo-PSLiteral $pythonExe) -m ncs_mcp.server --transport streamable-http --host $bindHost --port $port
"@

  Write-Host "Starting local NCS_MCP on http://$bindHost`:$port/mcp ..."
  $process = Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command) `
    -WindowStyle Hidden `
    -PassThru

  for ($i = 0; $i -lt 20; $i++) {
    if (Test-LocalPortOpen -HostName $bindHost -Port $port) {
      Write-Host "NCS_MCP is ready at $env:NCS_MCP_URL"
      return $process
    }
    Start-Sleep -Seconds 1
  }

  Write-Warning "NCS_MCP did not become reachable within 20 seconds. NCScope will still start."
  return $process
}

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

$startedMcpProcess = Start-LocalNcsMcpIfNeeded

Write-Host "Starting server on http://127.0.0.1:8015 ..."
Start-Job -ScriptBlock {
  Start-Sleep -Seconds 2
  Start-Process "http://127.0.0.1:8015"
} | Out-Null

try {
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8015
} finally {
  if ($startedMcpProcess -and -not $startedMcpProcess.HasExited) {
    Write-Host "Stopping local NCS_MCP process $($startedMcpProcess.Id) ..."
    try { Stop-Process -Id $startedMcpProcess.Id -Force } catch {}
  }
}
