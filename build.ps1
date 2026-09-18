[CmdletBinding()]
param(
    [ValidateSet("All", "x64", "x86")]
    [string]$Architecture = "All"
)

$ErrorActionPreference = "Stop"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$RustDir = $ScriptDir
$DistBase = $ScriptDir

if (Test-Path (Join-Path $ScriptDir "rust\Cargo.toml")) {
    $RustDir = Join-Path $ScriptDir "rust"
}

$Targets = @()
if ($Architecture -eq "All" -or $Architecture -eq "x64") {
    $Targets += [PSCustomObject]@{
        Arch = "x64"
        Target = "x86_64-pc-windows-msvc"
        Folder = "client-win7-x64"
        ZipName = "intervalcapture-win7-x64.zip"
        MesaDll = (Join-Path $RustDir "target\mesa-candidates\24.1.2\opengl32.dll")
        MesaAlt = (Join-Path $DistBase "target\mesa-candidates\24.1.2\opengl32.dll")
    }
}
if ($Architecture -eq "All" -or $Architecture -eq "x86") {
    $Targets += [PSCustomObject]@{
        Arch = "x86"
        Target = "i686-pc-windows-msvc"
        Folder = "client-win7-x86"
        ZipName = "intervalcapture-win7-x86.zip"
        MesaDll = (Join-Path $RustDir "target\mesa-candidates\x86-24.3.4\opengl32.dll")
        MesaAlt = (Join-Path $DistBase "target\mesa-candidates\x86-24.3.4\opengl32.dll")
    }
}

foreach ($item in $Targets) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host " Building intervalcapture ($($item.Arch))" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan

    # 1. Cargo 릴리스 빌드
    Write-Host "[1/4] cargo build --release --locked --target $($item.Target)..." -ForegroundColor Yellow
    Push-Location $RustDir
    try {
        cargo +1.77.2 build --release --locked --target $item.Target
    } finally {
        Pop-Location
    }

    $ExeSource = Join-Path $RustDir "target\$($item.Target)\release\intervalcapture.exe"
    if (-not (Test-Path $ExeSource)) {
        throw "빌드 실패: $ExeSource 파일을 찾을 수 없습니다."
    }

    # 2. 배포 폴더 구성
    Write-Host "[2/4] 배포 폴더 구성..." -ForegroundColor Yellow
    $DistDir = Join-Path $DistBase "dist"
    $PackDir = Join-Path $DistDir "pack\$($item.Folder)"
    $DistClientDir = Join-Path $DistDir "$($item.Folder)"

    Remove-Item -LiteralPath $PackDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $PackDir | Out-Null
    New-Item -ItemType Directory -Force -Path $DistClientDir | Out-Null

    Copy-Item $ExeSource (Join-Path $PackDir "intervalcapture.exe") -Force
    Copy-Item $ExeSource (Join-Path $DistClientDir "intervalcapture.exe") -Force

    $ActualMesa = if (Test-Path $item.MesaDll) { $item.MesaDll } elseif (Test-Path $item.MesaAlt) { $item.MesaAlt } else { $null }
    if ($ActualMesa) {
        Copy-Item $ActualMesa (Join-Path $PackDir "opengl32.dll") -Force
        Copy-Item $ActualMesa (Join-Path $DistClientDir "opengl32.dll") -Force
    } else {
        Write-Warning "경고: Mesa opengl32.dll 파일이 없어 복사하지 못했습니다."
    }

    # 3. 압축 (Bandizip 우선, 없을 시 Compress-Archive 폴백)
    Write-Host "[3/4] 배포 ZIP 파일 생성..." -ForegroundColor Yellow
    $ZipPath = Join-Path $DistDir "$($item.ZipName)"
    $Bandizip = "C:\Program Files\Bandizip\bz.exe"

    if (Test-Path $Bandizip) {
        Push-Location (Join-Path $DistDir "pack")
        try {
            & $Bandizip c -y -r -l:9 "..\\$($item.ZipName)" "$($item.Folder)" | Out-Null
        } finally {
            Pop-Location
        }
    } else {
        Compress-Archive -Path "$PackDir" -DestinationPath $ZipPath -Force
    }

    # 4. 검증
    Write-Host "[4/4] 배포 산출물 검증..." -ForegroundColor Green
    $ExeItem = Get-Item (Join-Path $DistClientDir "intervalcapture.exe")
    $ZipItem = Get-Item $ZipPath
    $Hash = (Get-FileHash $ExeItem.FullName -Algorithm SHA256).Hash

    # PE 헤더 확인 (Subsystem)
    $Bytes = [System.IO.File]::ReadAllBytes($ExeItem.FullName)
    $PeOffset = [System.BitConverter]::ToInt32($Bytes, 0x3C)
    $Subsystem = [System.BitConverter]::ToUInt16($Bytes, $PeOffset + 0x5C)
    $SubsystemText = if ($Subsystem -eq 2) { "GUI (콘솔창 없음, OK)" } else { "Unknown ($Subsystem)" }

    Write-Host "  - 타깃:       $($item.Target)"
    Write-Host "  - 서브시스템: $SubsystemText"
    Write-Host "  - EXE 크기:   $([math]::Round($ExeItem.Length / 1MB, 2)) MB ($($ExeItem.Length) bytes)"
    Write-Host "  - EXE SHA256: $Hash"
    Write-Host "  - ZIP 산출물: $($ZipItem.FullName) ($([math]::Round($ZipItem.Length / 1MB, 2)) MB)"
}

Write-Host "`n========================================" -ForegroundColor Green
Write-Host " 모든 빌드 및 패키징이 성공적으로 완료되었습니다!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
