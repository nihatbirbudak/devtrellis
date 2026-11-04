# Yerel radarı gizli bir süreçte başlatır; zaten açıksa aynı uygulamayı kullanır.
param([switch]$TarayiciAcma, [switch]$ElleToplama, [ValidateRange(1024,65535)][int]$Port = 8781)
$ErrorActionPreference = 'Stop'
$radarRoot = $PSScriptRoot
$radarUrl = "http://127.0.0.1:$Port"
$radarData = Join-Path $radarRoot 'data'
$radarIdentity = Join-Path $radarData "surec-$Port.json"
$radarMutex = New-Object System.Threading.Mutex($false, "Local\GelismeRadari-$Port")
$radarHeld = $false
$radarProcess = $null
$radarReady = $false
function Test-Radar {
    try {
        $radarHealth = Invoke-RestMethod -Uri "$radarUrl/api/health" -TimeoutSec 2
        return ($radarHealth.application -eq 'gelisme-radari' -and $radarHealth.status -eq 'ok')
    } catch { return $false }
}
try {
    # İkinci çift tıklama, ilk açılış tamamlandığında aynı süreci kullanır.
    try { $radarHeld = $radarMutex.WaitOne(30000) } catch [System.Threading.AbandonedMutexException] { $radarHeld = $true }
    if (-not $radarHeld) { throw 'Radarın diğer açılışı henüz tamamlanmadı. Biraz sonra tekrar açın.' }
    $radarStopScript = Join-Path $radarRoot 'durdur.ps1'
    $radarExisting = & $radarStopScript -Port $Port -YalnizDogrula
    if ($radarExisting) {
        if (-not (Test-Radar)) { throw 'Kayıtlı radar süreci çalışıyor fakat sağlık denetimi başarısız; ikinci süreç açılmadı.' }
        Write-Output "Radar zaten açık: $radarUrl"
        if (-not $TarayiciAcma) { Start-Process $radarUrl }
        return
    }
    # Kimliği olmayan sağlıklı bir sunucu da bu başlatıcının süreci sayılmaz.
    $radarProbe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
    try {
        $radarProbe.Server.ExclusiveAddressUse = $true
        $radarProbe.Start()
    } catch { throw "127.0.0.1:$Port başka bir süreç tarafından kullanılıyor; yeni radar açılmadı." }
    finally { $radarProbe.Stop() }
    $radarCandidates = @(
        (Join-Path $radarRoot '.venv\Scripts\python.exe')
    )
    $radarCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($radarCommand) { $radarCandidates += $radarCommand.Source }
    $radarScript = Join-Path $radarRoot 'calistir.py'
    if (-not (Test-Path -LiteralPath $radarScript -PathType Leaf)) { throw 'Radar uygulama dosyası bulunamadı.' }
    $radarRuntimeCheck = "import sys; from pathlib import Path; assert sys.version_info >= (3, 12), 'Python 3.12 veya ustu gerekli'; sys.path.insert(0, str(Path(sys.argv[1]) / '.deps')); import fastapi, uvicorn, feedparser, bs4, httpx, keyring"
    $radarPython = $null
    foreach ($radarCandidate in ($radarCandidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $radarCandidate -PathType Leaf)) { continue }
        try {
            $radarRuntimeOutput = & $radarCandidate -X utf8 -c $radarRuntimeCheck $radarRoot 2>&1
            if ($LASTEXITCODE -eq 0) { $radarPython = $radarCandidate; break }
        } catch { continue }
    }
    if (-not $radarPython) { throw 'Çalışır Python 3.12 veya DevTrellis bağımlılıkları bulunamadı. README.md içindeki kurulum adımını inceleyin.' }
    New-Item -ItemType Directory -Path $radarData -Force | Out-Null
    $radarArguments = @('-X', 'utf8', ('"' + $radarScript + '"'), 'sun', '--port', "$Port")
    if ($ElleToplama) { $radarArguments += '--elle-toplama' }
    $radarProcess = Start-Process -FilePath $radarPython -ArgumentList $radarArguments -WorkingDirectory $radarRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $radarData "sunucu-$Port.log") -RedirectStandardError (Join-Path $radarData "hata-$Port.log") -PassThru
    $radarStarted = $radarProcess.StartTime.ToUniversalTime().ToString('o')
    @{ pid = $radarProcess.Id; started_at = $radarStarted; script = $radarScript; url = $radarUrl } | ConvertTo-Json | Set-Content -LiteralPath $radarIdentity -Encoding UTF8
    for ($radarTry = 0; $radarTry -lt 40; $radarTry++) {
        if ($radarProcess.HasExited) { break }
        if (Test-Radar) { $radarReady = $true; break }
        Start-Sleep -Milliseconds 250
        $radarProcess.Refresh()
    }
    if (-not $radarReady) { throw "Radar açılamadı. $radarData\hata-$Port.log dosyasını inceleyin." }
    $radarVerified = & $radarStopScript -Port $Port -YalnizDogrula
    if (-not $radarVerified -or $radarVerified.Id -ne $radarProcess.Id) {
        $radarReady = $false
        throw 'Açılan radar süreci doğrulanamadı.'
    }
    Write-Output "Radar hazır: $radarUrl"
    if (-not $TarayiciAcma) { Start-Process $radarUrl }
} catch {
    if ($radarProcess) {
        # Yalnız bu çağrının yeni açtığı süreç temizlenir; mevcut süreçlere dokunulmaz.
        $radarCleanup = @($radarProcess)
        if (Test-Path -LiteralPath $radarIdentity) {
            $radarVerified = & $radarStopScript -Port $Port -YalnizDogrula
            if ($radarVerified -and $radarVerified.Id -eq $radarProcess.Id) {
                $radarCleanup = @($radarVerified.RadarAltSurecler) + @($radarProcess)
            }
        }
        foreach ($radarOwned in $radarCleanup) {
            $radarOwned.Refresh()
            if (-not $radarOwned.HasExited) {
                try { $radarOwned.Kill() }
                catch {
                    $radarOwned.Refresh()
                    if (-not $radarOwned.HasExited) { throw }
                }
            }
            if (-not $radarOwned.WaitForExit(5000)) { throw 'Başlatılamayan radar kapanmadı; kimlik kaydı korundu.' }
        }
        if (Test-Path -LiteralPath $radarIdentity) { Remove-Item -LiteralPath $radarIdentity -Force }
    }
    throw
} finally {
    if ($radarHeld) { $radarMutex.ReleaseMutex() }
    $radarMutex.Dispose()
}
