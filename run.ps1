$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$PORT = 130000

Write-Host "=========================================="
Write-Host "WebRTC Screen Share + Control Launcher"
Write-Host "=========================================="
Write-Host ""

# ---------------------------------------------------------
# Detect Python command: prefer py, fallback to python
# ---------------------------------------------------------
$pythonCmd = $null

if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
}

if (-not $pythonCmd) {
    Write-Host '[ERROR] Neither "py" nor "python" was found.' -ForegroundColor Red
    Write-Host '[ERROR] Install Python 3.10+ and make sure it is added to PATH.' -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[INFO] Using Python command: $pythonCmd" -ForegroundColor Cyan

# ---------------------------------------------------------
# Create virtual environment if needed
# ---------------------------------------------------------
if (-not (Test-Path ".venv")) {
    Write-Host "[INFO] Creating virtual environment..." -ForegroundColor Cyan
    & $pythonCmd -m venv .venv
}

# ---------------------------------------------------------
# Activate virtual environment
# ---------------------------------------------------------
$activatePath = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $activatePath)) {
    Write-Host "[ERROR] Failed to find the virtual environment activation script." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

. $activatePath

# ---------------------------------------------------------
# Upgrade pip and install dependencies
# ---------------------------------------------------------
Write-Host "[INFO] Upgrading pip..." -ForegroundColor Cyan
python -m pip install --upgrade pip

Write-Host "[INFO] Installing dependencies from requirements.txt..." -ForegroundColor Cyan
python -m pip install -r requirements.txt

# ---------------------------------------------------------
# Read runtime options
# ---------------------------------------------------------
$accessToken = Read-Host "Enter view token [default: 91199837]"
if ([string]::IsNullOrWhiteSpace($accessToken)) {
    $accessToken = "91199837"
}

$controlToken = Read-Host "Enter control token [default: 91199837]"
if ([string]::IsNullOrWhiteSpace($controlToken)) {
    $controlToken = "91199837"
}

$monitorIndex = Read-Host "Enter monitor index [default: 1]"
if ([string]::IsNullOrWhiteSpace($monitorIndex)) {
    $monitorIndex = "1"
}

$fps = Read-Host "Enter FPS [default: 15]"
if ([string]::IsNullOrWhiteSpace($fps)) {
    $fps = "15"
}

$scale = Read-Host "Enter scale factor [default: 2]"
if ([string]::IsNullOrWhiteSpace($scale)) {
    $scale = "2"
}

# ---------------------------------------------------------
# Detect local LAN IPv4 address
# ---------------------------------------------------------
$serverIP = $null

try {
    $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric |
        Select-Object -First 1

    if ($route) {
        $serverIP = Get-NetIPAddress -InterfaceIndex $route.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object {
                $_.IPAddress -notlike "169.254.*" -and $_.IPAddress -notlike "127.*"
            } |
            Select-Object -First 1 -ExpandProperty IPAddress
    }
}
catch {
    $serverIP = $null
}

if (-not $serverIP) {
    try {
        $serverIP = (ipconfig | Select-String -Pattern "IPv4 Address|IPv4 주소" | Select-Object -First 1).ToString().Split(":")[-1].Trim()
    }
    catch {
        $serverIP = $null
    }
}

# ---------------------------------------------------------
# Show launch information
# ---------------------------------------------------------
Write-Host ""
Write-Host "========================================================="
Write-Host "Server launch information"
Write-Host "========================================================="
Write-Host "[INFO] Local test URL : http://127.0.0.1:$PORT" -ForegroundColor Green

if ($serverIP) {
    Write-Host "[INFO] LAN access URL : http://$serverIP`:$PORT" -ForegroundColor Green
}
else {
    Write-Host "[WARN] LAN IPv4 address could not be detected automatically." -ForegroundColor Yellow
    Write-Host "[WARN] Run ipconfig manually and use your IPv4 address." -ForegroundColor Yellow
}

Write-Host "[INFO] View token     : $accessToken"
Write-Host "[INFO] Control token  : $controlToken"
Write-Host "[INFO] Monitor index  : $monitorIndex"
Write-Host "[INFO] FPS            : $fps"
Write-Host "[INFO] Scale          : $scale"
Write-Host "========================================================="
Write-Host ""

Write-Host "[INFO] Starting server..." -ForegroundColor Cyan
Write-Host ""

python server.py `
    --host 0.0.0.0 `
    --port $PORT `
    --monitor $monitorIndex `
    --fps $fps `
    --scale $scale `
    --access-token $accessToken `
    --control-token $controlToken

Write-Host ""
Write-Host "[INFO] Server stopped." -ForegroundColor Cyan
Read-Host "Press Enter to exit"