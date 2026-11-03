<#
Brings the stack up on a Windows dev machine: starts Docker Desktop if the engine is down, runs
`docker compose up -d --build`, and waits until the service reports healthy.

Usage (any directory): .\scripts\dev-up.ps1 [-NoBuild] [-TimeoutSec 600]
Servers run Docker Engine as a boot service, so they only need `docker compose up -d`.
#>
param(
    [switch]$NoBuild,
    [int]$TimeoutSec = 600  # a first start downloads about 1 GB of model
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

function Test-Engine {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    docker info *> $null
    $ok = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $previous
    return $ok
}

function Find-DockerDesktop {
    $roots = @("$env:ProgramFiles\Docker\Docker", "$env:LOCALAPPDATA\Programs\DockerDesktop")
    $cli = Get-Command docker -ErrorAction SilentlyContinue
    if ($cli) { $roots = @(Split-Path (Split-Path (Split-Path $cli.Source))) + $roots }  # ...\resources\bin\docker.exe
    foreach ($root in $roots) {
        $exe = Join-Path $root 'Docker Desktop.exe'
        if (Test-Path $exe) { return $exe }
    }
    throw 'Docker Desktop was not found; install it or start the Docker engine yourself.'
}

function Wait-Until([scriptblock]$Ready, [string]$What) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while (-not (& $Ready)) {
        if ((Get-Date) -gt $deadline) { throw "Timed out after $TimeoutSec s waiting for $What." }
        Start-Sleep -Seconds 3
    }
}

if (-not (Test-Engine)) {
    Write-Host 'Starting Docker Desktop...'
    Start-Process (Find-DockerDesktop)
    Wait-Until { Test-Engine } 'the Docker engine'
}

$composeArgs = @('compose', 'up', '-d') + $(if ($NoBuild) { @() } else { @('--build') })
docker @composeArgs
if ($LASTEXITCODE -ne 0) { throw 'docker compose failed.' }

Write-Host 'Waiting for the service (the embedder loads the model first)...'
Wait-Until {
    try { (Invoke-WebRequest -Uri 'http://localhost:8000/health' -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
    catch { $false }
} 'the service health check'

Write-Host 'Ready: http://localhost:8000/demo  (API docs: http://localhost:8000/docs)'
