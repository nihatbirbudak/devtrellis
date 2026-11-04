# Yalnız bu klasörün başlatıcısının açtığı ve kimliği doğrulanan radar sürecini durdurur.
param([ValidateRange(1024,65535)][int]$Port = 8781, [switch]$YalnizDogrula)
$ErrorActionPreference = 'Stop'
$radarIdentity = Join-Path $PSScriptRoot "data\surec-$Port.json"
function Test-RadarCommand($radarCandidate, $radarCommandLine, $radarParentPython = '') {
    $radarPythonPath = $radarCandidate.Path
    if (-not $radarPythonPath -or [IO.Path]::GetFileName($radarPythonPath) -ne 'python.exe') { return $false }
    $radarPaths = @($radarPythonPath)
    if ($radarParentPython) { $radarPaths += $radarParentPython }
    $radarPatternPaths = ($radarPaths | Select-Object -Unique | ForEach-Object { [regex]::Escape($_) }) -join '|'
    $radarCommandPattern = '^"?(?:' + $radarPatternPaths + ')"?\s+-X\s+utf8\s+"' +
        [regex]::Escape((Join-Path $PSScriptRoot 'calistir.py')) + '"\s+sun\s+--port\s+' + $Port + '(?:\s+--elle-toplama)?\s*$'
    return ($radarCommandLine -and $radarCommandLine -match $radarCommandPattern)
}
function Get-RadarProcess {
    if (-not (Test-Path -LiteralPath $radarIdentity)) { return $null }
    try { $radarRecord = Get-Content -LiteralPath $radarIdentity -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw 'Süreç kaydı okunamadı; güvenli kapatma için data klasöründeki süreç kaydı denetlenmeli.' }
    $radarExpectedScript = Join-Path $PSScriptRoot 'calistir.py'
    $radarProcessId = 0
    if ($radarRecord.script -ne $radarExpectedScript -or $radarRecord.url -ne "http://127.0.0.1:$Port" -or
        -not [int]::TryParse([string]$radarRecord.pid, [ref]$radarProcessId) -or $radarProcessId -le 0) {
        throw 'Süreç kaydı bu radar klasörüne ve bağlantı noktasına ait değil; işlem yapılmadı.'
    }
    $radarProcess = Get-Process -Id $radarProcessId -ErrorAction SilentlyContinue
    if (-not $radarProcess) { return $null }
    # PowerShell 7 JSON tarihini DateTime, 5.1 ise metin olarak okuyabilir.
    try { $radarExpectedTicks = ([datetimeoffset]$radarRecord.started_at).UtcDateTime.Ticks }
    catch { throw 'Süreç kaydındaki başlangıç zamanı geçersiz; işlem yapılmadı.' }
    if ($radarProcess.StartTime.ToUniversalTime().Ticks -ne $radarExpectedTicks) {
        throw 'Süreç numarası başka bir uygulama tarafından kullanılıyor; işlem yapılmadı.'
    }
    $radarCommand = Get-CimInstance Win32_Process -Filter "ProcessId=$radarProcessId" -ErrorAction Stop
    if (-not (Test-RadarCommand $radarProcess $radarCommand.CommandLine)) {
        throw 'Süreç komutu doğrulanamadı; işlem yapılmadı.'
    }
    # Windows sanal ortamı gerçek Python'u alt süreç olarak çalıştırabilir.
    # Yalnız aynı uygulama komutuyla açılan doğrudan alt süreçler sahiplenilir.
    $radarChildren = @()
    foreach ($radarChildCommand in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$radarProcessId AND Name='python.exe'" -ErrorAction Stop)) {
        $radarChild = Get-Process -Id $radarChildCommand.ProcessId -ErrorAction SilentlyContinue
        if (-not $radarChild) { continue }
        if ($radarChild.StartTime -lt $radarProcess.StartTime -or
            -not (Test-RadarCommand $radarChild $radarChildCommand.CommandLine $radarProcess.Path)) {
            throw 'Alt süreç bu radar uygulaması olarak doğrulanamadı; işlem yapılmadı.'
        }
        $radarChildren += $radarChild
    }
    $radarProcess | Add-Member -NotePropertyName RadarAltSurecler -NotePropertyValue $radarChildren -Force
    return $radarProcess
}

# Başlatıcı aynı doğrulamayı kendi tuttuğu kilit altında kullanır.
if ($YalnizDogrula) { return Get-RadarProcess }
$radarMutex = New-Object System.Threading.Mutex($false, "Local\GelismeRadari-$Port")
$radarHeld = $false
try {
    try { $radarHeld = $radarMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $radarHeld = $true }
    if (-not $radarHeld) { throw 'Radar başka bir başlatıcı tarafından yönetiliyor.' }
    $radarProcess = Get-RadarProcess
    if ($radarProcess) {
        foreach ($radarOwned in @($radarProcess.RadarAltSurecler) + @($radarProcess)) {
            $radarOwned.Refresh()
            if (-not $radarOwned.HasExited) {
                try { $radarOwned.Kill() }
                catch {
                    $radarOwned.Refresh()
                    if (-not $radarOwned.HasExited) { throw }
                }
            }
            if (-not $radarOwned.WaitForExit(5000)) { throw 'Radar süreci henüz kapanmadı; kimlik kaydı korundu.' }
        }
        Write-Output 'Yerel radar kapatıldı. Kayıtlar data klasöründe korunuyor.'
    } else {
        Write-Output 'Radar zaten kapalı; başlatıcıya ait çalışan süreç yok.'
    }
    if (Test-Path -LiteralPath $radarIdentity) { Remove-Item -LiteralPath $radarIdentity -Force }
} finally {
    if ($radarHeld) { $radarMutex.ReleaseMutex() }
    $radarMutex.Dispose()
}
