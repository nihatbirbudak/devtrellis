# UFUK'u oturum açılışında ve günlük saatte başlatan kullanıcı görevini kurar, inceler veya kaldırır.
[CmdletBinding()]
param(
    [ValidateSet('Kur', 'Kaldir', 'Durum')][string]$Islem = 'Durum',
    [ValidatePattern('^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')][string]$Saat = '08:00'
)
$ErrorActionPreference = 'Stop'
$ufukRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$ufukLauncher = Join-Path $ufukRoot 'baslat.ps1'
$ufukPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$ufukTaskName = 'UFUK Günlük Takip'
$ufukTaskPath = '\'
$ufukIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$ufukUserId = $ufukIdentity.User.Value
$ufukUserName = $ufukIdentity.Name
$ufukUserAliases = @($ufukUserId, $ufukUserName)
$ufukAccountParts = $ufukUserName.Split('\', 2)
# Windows, yerel hesabın SID'sini görevden okurken yalnız kullanıcı adı döndürebilir.
if ($ufukAccountParts.Count -eq 2 -and
    [string]::Equals($ufukAccountParts[0], $env:COMPUTERNAME, [System.StringComparison]::OrdinalIgnoreCase)) {
    $ufukUserAliases += $ufukAccountParts[1]
}
$ufukArguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $ufukLauncher + '" -TarayiciAcma'
$ufukHash = [System.Security.Cryptography.SHA256]::Create()
try {
    $ufukBytes = [System.Text.Encoding]::UTF8.GetBytes(($ufukRoot + '|' + $ufukUserId).ToLowerInvariant())
    $ufukMarker = 'UFUK-GUNLUK-TAKIP-V1:' + ([System.BitConverter]::ToString($ufukHash.ComputeHash($ufukBytes))).Replace('-', '')
} finally { $ufukHash.Dispose() }
$ufukDescription = $ufukMarker + "`nUFUK uygulamasını bu kullanıcının açık oturumunda gizli başlatır. Araştırma takvimi uygulamada yönetilir. Klasör: " + $ufukRoot

function Test-UfukTaskOwner($Task) {
    if (-not $Task -or $Task.TaskName -cne $ufukTaskName -or $Task.TaskPath -ne $ufukTaskPath) { return $false }
    if (-not $Task.Description -or ($Task.Description -split '\r?\n', 2)[0] -cne $ufukMarker) { return $false }
    $ufukActions = @($Task.Actions)
    if ($ufukActions.Count -ne 1) { return $false }
    if (-not [string]::Equals($ufukActions[0].Execute, $ufukPowerShell, [System.StringComparison]::OrdinalIgnoreCase) -or
        $ufukActions[0].Arguments -cne $ufukArguments -or
        -not [string]::Equals($ufukActions[0].WorkingDirectory, $ufukRoot, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    if ($Task.Principal.UserId -notin $ufukUserAliases -or
        [string]$Task.Principal.LogonType -ne 'Interactive' -or
        [string]$Task.Principal.RunLevel -ne 'Limited') { return $false }
    return $true
}

foreach ($ufukFile in @($ufukLauncher, (Join-Path $ufukRoot 'durdur.ps1'), (Join-Path $ufukRoot 'calistir.py'), $ufukPowerShell)) {
    if (-not (Test-Path -LiteralPath $ufukFile -PathType Leaf)) { throw 'UFUK başlatıcısı veya Windows PowerShell bulunamadı. Betiği kurulu uygulamanın araclar klasöründen çalıştırın.' }
}
foreach ($ufukCommand in @('Get-ScheduledTask', 'Get-ScheduledTaskInfo', 'New-ScheduledTaskAction', 'New-ScheduledTaskTrigger',
                           'New-ScheduledTaskPrincipal', 'New-ScheduledTaskSettingsSet', 'Register-ScheduledTask', 'Unregister-ScheduledTask')) {
    if (-not (Get-Command $ufukCommand -ErrorAction SilentlyContinue)) { throw 'Windows Görev Zamanlayıcı komutları bulunamadı. Windows PowerShell kullanın.' }
}
try {
    $ufukExisting = Get-ScheduledTask -TaskName $ufukTaskName -TaskPath $ufukTaskPath -ErrorAction Stop
} catch {
    if ($_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*') { $ufukExisting = $null }
    else { throw 'Windows Görev Zamanlayıcıya erişilemedi. Betiği kendi Windows oturumunuzdaki PowerShell penceresinde çalıştırın; mevcut görevlerde değişiklik yapılmadı.' }
}
$ufukOwned = Test-UfukTaskOwner $ufukExisting
if ($ufukExisting -and -not $ufukOwned -and $Islem -ne 'Durum') {
    throw 'Aynı adlı görev bu UFUK klasörüne ve kullanıcıya ait olarak doğrulanamadı. Görev değiştirilmedi veya kaldırılmadı.'
}

if ($Islem -eq 'Kur') {
    $ufukClock = [datetime]::Today.AddHours([int]$Saat.Substring(0, 2)).AddMinutes([int]$Saat.Substring(3, 2))
    if ($ufukClock -le (Get-Date)) { $ufukClock = $ufukClock.AddDays(1) }
    $ufukAction = New-ScheduledTaskAction -Execute $ufukPowerShell -Argument $ufukArguments -WorkingDirectory $ufukRoot
    $ufukDaily = New-ScheduledTaskTrigger -Daily -At $ufukClock
    $ufukLogon = New-ScheduledTaskTrigger -AtLogOn -User $ufukUserId
    $ufukPrincipal = New-ScheduledTaskPrincipal -UserId $ufukUserId -LogonType Interactive -RunLevel Limited
    # Kalıcı uygulama süreci Windows'un görev süresi sınırı yüzünden kesilmemelidir.
    $ufukSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit ([timespan]::Zero) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1)
    $ufukRegistration = @{
        TaskName = $ufukTaskName; TaskPath = $ufukTaskPath; Action = $ufukAction; Trigger = @($ufukDaily, $ufukLogon)
        Principal = $ufukPrincipal; Settings = $ufukSettings; Description = $ufukDescription; ErrorAction = 'Stop'
    }
    # Yeni görevde Force kullanılmaz: denetimden sonra aynı adı alan başka bir görev ezilmez.
    if ($ufukExisting) { $ufukRegistration.Force = $true }
    try { $ufukInstalled = Register-ScheduledTask @ufukRegistration }
    catch { throw 'UFUK günlük görevi kaydedilemedi. Yönetici yetkisi veya parola kullanılmadı; uygulamanın elle başlatıcısı kullanılabilir. Kendi Windows oturumunuzdaki Görev Zamanlayıcı izinlerini denetleyin.' }
    if (-not (Test-UfukTaskOwner $ufukInstalled)) { throw 'Kaydedilen görevin UFUK kimliği doğrulanamadı. Durum komutuyla denetleyin.' }
    [pscustomobject]@{ islem = 'kuruldu'; task_name = $ufukTaskName; task_path = $ufukTaskPath; owned = $true
        daily_time = $Saat; timezone = [System.TimeZoneInfo]::Local.Id; at_logon = $true; user_id = $ufukUserId
        logon_type = 'Interactive'; run_level = 'Limited'; launcher = $ufukLauncher; arguments = $ufukArguments
        start_when_available = $true; application_started = $false }
    return
}

if ($Islem -eq 'Kaldir') {
    if ($ufukExisting) { Unregister-ScheduledTask -InputObject $ufukExisting -Confirm:$false -ErrorAction Stop }
    [pscustomobject]@{ islem = 'kaldirildi'; task_name = $ufukTaskName; existed = [bool]$ufukExisting
        application_stopped = $false; files_deleted = $false }
    return
}

$ufukStatus = [ordered]@{ islem = 'durum'; task_name = $ufukTaskName; task_path = $ufukTaskPath
    installed = [bool]$ufukExisting; owned = [bool]$ufukOwned; launcher = $ufukLauncher; user_id = $ufukUserId
    timezone = [System.TimeZoneInfo]::Local.Id; expected_arguments = $ufukArguments }
if ($ufukExisting) {
    $ufukStatus.state = [string]$ufukExisting.State
    if ($ufukOwned) {
        $ufukInfo = Get-ScheduledTaskInfo -InputObject $ufukExisting -ErrorAction Stop
        $ufukStatus.last_run_time = $ufukInfo.LastRunTime.ToString('o')
        $ufukStatus.next_run_time = $ufukInfo.NextRunTime.ToString('o')
        $ufukStatus.last_task_result = $ufukInfo.LastTaskResult
        $ufukStatus.missed_runs = $ufukInfo.NumberOfMissedRuns
        $ufukStatus.triggers = @($ufukExisting.Triggers | ForEach-Object {
            [pscustomobject]@{ kind = $_.CimClass.CimClassName; start_boundary = $_.StartBoundary; user_id = $_.UserId }
        })
    } else { $ufukStatus.message = 'Aynı adlı görev bu kurulumun kimliğiyle eşleşmiyor; değiştirilmedi.' }
}
[pscustomobject]$ufukStatus
