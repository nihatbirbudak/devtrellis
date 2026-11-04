"""Windows günlük başlatıcısının görev kapsamını ve seçeneklerini gerçek PowerShell, sahte görev komutlarıyla doğrular."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


KOK = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which(os.environ.get("UFUK_TEST_POWERSHELL", "powershell.exe"))
BETIK = KOK / "araclar" / "otomatik-gorev.ps1"
SAHTE_KOMUTLAR = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$global:UfukCalls = [System.Collections.Generic.List[object]]::new()
$global:UfukTask = $null
$global:UfukQueryError = $false
$global:UfukQueryMissing = $false
$global:UfukShortUserName = $false
$global:UfukRegisterError = $false
function Save-UfukCall($Name, $Parameters) {
    $values = @{}
    foreach ($entry in $Parameters.GetEnumerator()) {
        $value = $entry.Value
        if ($value -is [System.Management.Automation.SwitchParameter]) { $value = $value.IsPresent }
        if ($value -is [timespan]) { $value = $value.TotalSeconds }
        $values[$entry.Key] = $value
    }
    $global:UfukCalls.Add([pscustomobject]@{ name = $Name; parameters = $values })
}
function Get-ScheduledTask {
    [CmdletBinding()]param($TaskName, $TaskPath)
    Save-UfukCall 'get' $PSBoundParameters
    if ($global:UfukQueryError) { throw 'Test: scheduler access denied' }
    if ($global:UfukQueryMissing) {
        throw [System.Management.Automation.ErrorRecord]::new([System.Exception]::new('Task missing'),
            'CmdletizationQuery_NotFound', [System.Management.Automation.ErrorCategory]::ObjectNotFound, $TaskName)
    }
    return $global:UfukTask
}
function Get-ScheduledTaskInfo {
    [CmdletBinding()]param($InputObject)
    Save-UfukCall 'info' $PSBoundParameters
    [pscustomobject]@{ LastRunTime = [datetime]'2026-10-26T08:00:00'; NextRunTime = [datetime]'2026-10-27T08:00:00'; LastTaskResult = 0; NumberOfMissedRuns = 1 }
}
function New-ScheduledTaskAction {
    [CmdletBinding()]param($Execute, $Argument, $WorkingDirectory)
    Save-UfukCall 'action' $PSBoundParameters
    [pscustomobject]@{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory }
}
function New-ScheduledTaskTrigger {
    [CmdletBinding()]param([switch]$Daily, [datetime]$At, [switch]$AtLogOn, $User)
    Save-UfukCall 'trigger' $PSBoundParameters
    [pscustomobject]@{ CimClass = @{ CimClassName = $(if ($Daily) { 'MSFT_TaskDailyTrigger' } else { 'MSFT_TaskLogonTrigger' }) }
        StartBoundary = $(if ($Daily) { $At.ToString('o') } else { $null }); UserId = $User }
}
function New-ScheduledTaskPrincipal {
    [CmdletBinding()]param($UserId, $LogonType, $RunLevel)
    Save-UfukCall 'principal' $PSBoundParameters
    if ($global:UfukShortUserName) { $UserId = [Environment]::UserName }
    [pscustomobject]@{ UserId = $UserId; LogonType = $LogonType; RunLevel = $RunLevel }
}
function New-ScheduledTaskSettingsSet {
    [CmdletBinding()]param([switch]$StartWhenAvailable, [switch]$AllowStartIfOnBatteries,
        [switch]$DontStopIfGoingOnBatteries, $MultipleInstances, [timespan]$ExecutionTimeLimit,
        [int]$RestartCount, [timespan]$RestartInterval)
    Save-UfukCall 'settings' $PSBoundParameters
    [pscustomobject]@{ StartWhenAvailable = $StartWhenAvailable.IsPresent; MultipleInstances = $MultipleInstances }
}
function Register-ScheduledTask {
    [CmdletBinding()]param($TaskName, $TaskPath, $Action, $Trigger, $Principal, $Settings, $Description, [switch]$Force)
    Save-UfukCall 'register' $PSBoundParameters
    if ($global:UfukRegisterError) { throw 'Test: registration denied' }
    if ($global:UfukTask -and -not $Force) { throw 'Test: existing task must not be overwritten' }
    $global:UfukTask = [pscustomobject]@{ TaskName = $TaskName; TaskPath = $TaskPath; Actions = @($Action)
        Triggers = @($Trigger); Principal = $Principal; Settings = $Settings; Description = $Description; State = 'Ready' }
    return $global:UfukTask
}
function Unregister-ScheduledTask {
    [CmdletBinding()]param($InputObject, [switch]$Confirm)
    Save-UfukCall 'unregister' $PSBoundParameters
    $global:UfukTask = $null
}
function Start-ScheduledTask { throw 'Test: task execution is forbidden' }
function Start-Process { throw 'Test: starting processes is forbidden' }
function Stop-Process { throw 'Test: stopping processes is forbidden' }
function Invoke-RestMethod { throw 'Test: network requests are forbidden' }
function Remove-Item { throw 'Test: file deletion is forbidden' }
'''


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell gerekir; gerçek görev kurulmaz.")
class OtomatikGorevTestleri(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="ufuk-gunluk-gorev-")
        cls.root = Path(cls.temporary.name) / "Türkçe boşluklu UFUK"
        (cls.root / "araclar").mkdir(parents=True)
        cls.script = cls.root / "araclar" / "otomatik-gorev.ps1"
        shutil.copy2(BETIK, cls.script)
        for filename in ("baslat.ps1", "durdur.ps1", "calistir.py"):
            (cls.root / filename).write_text("# Bu testte çalıştırılmaz.\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def invoke(self, scenario):
        driver = self.root / "prova.ps1"
        driver.write_text(SAHTE_KOMUTLAR + "\ntry {\n" + scenario + r'''
    [pscustomobject]@{ ok = $true; result = $result; calls = @($global:UfukCalls.ToArray()) } | ConvertTo-Json -Depth 12 -Compress
} catch {
    [pscustomobject]@{ ok = $false; error = $_.Exception.Message; calls = @($global:UfukCalls.ToArray()) } | ConvertTo-Json -Depth 12 -Compress
}
''', encoding="utf-8-sig")
        environment = {**os.environ, "UFUK_AUTOMATIC_SCRIPT": str(self.script)}
        completed = subprocess.run([POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                    "-File", str(driver)], env=environment, cwd=self.root, capture_output=True,
                                   encoding="utf-8-sig", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return json.loads(completed.stdout)

    def calls(self, response, name):
        return [entry["parameters"] for entry in response["calls"] if entry["name"] == name]

    def test_powershell_sozdizimi_ve_varsayilan_durum_salt_okunur(self):
        parsed = self.invoke(r'''
$tokens = $null; $errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile($env:UFUK_AUTOMATIC_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$result = & $env:UFUK_AUTOMATIC_SCRIPT
''')
        self.assertTrue(parsed["ok"], parsed.get("error"))
        self.assertFalse(parsed["result"]["installed"])
        self.assertEqual([call["name"] for call in parsed["calls"]], ["get"])
        self.assertEqual(self.calls(parsed, "get")[0]["TaskPath"], "\\")

    def test_kurulum_yalniz_kendi_kullanicisi_ve_gizli_baslaticisini_kaydeder(self):
        response = self.invoke("$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur")
        self.assertTrue(response["ok"], response.get("error"))
        result = response["result"]
        self.assertEqual(result["task_name"], "UFUK Günlük Takip")
        self.assertEqual(result["daily_time"], "08:00")
        self.assertFalse(result["application_started"])
        action = self.calls(response, "action")[0]
        self.assertEqual(Path(action["WorkingDirectory"]).resolve(), self.root.resolve())
        self.assertIn('-File "' + str((self.root / "baslat.ps1").resolve()) + '" -TarayiciAcma', action["Argument"])
        for argument in ("-WindowStyle Hidden", "-NoProfile", "-NonInteractive"):
            self.assertIn(argument, action["Argument"])
        principal = self.calls(response, "principal")[0]
        self.assertEqual(principal, {"UserId": result["user_id"], "LogonType": "Interactive", "RunLevel": "Limited"})
        registration = self.calls(response, "register")[0]
        self.assertNotIn("Force", registration)
        self.assertNotIn("Password", registration)
        self.assertEqual(registration["TaskName"], result["task_name"])
        self.assertEqual(registration["TaskPath"], "\\")
        self.assertTrue(registration["Description"].startswith("UFUK-GUNLUK-TAKIP-V1:"))

    def test_gunluk_saat_oturum_acilis_ve_kacirilan_calisma_ayarlari(self):
        response = self.invoke("$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur -Saat 06:45")
        self.assertTrue(response["ok"], response.get("error"))
        triggers = self.calls(response, "trigger")
        self.assertEqual(len(triggers), 2)
        self.assertTrue(triggers[0]["Daily"])
        self.assertTrue(triggers[0]["At"].startswith("/Date("))
        registration = self.calls(response, "register")[0]
        self.assertIn("T06:45:00", registration["Trigger"][0]["StartBoundary"])
        self.assertTrue(triggers[1]["AtLogOn"])
        self.assertEqual(triggers[1]["User"], response["result"]["user_id"])
        settings = self.calls(response, "settings")[0]
        for key in ("StartWhenAvailable", "AllowStartIfOnBatteries", "DontStopIfGoingOnBatteries"):
            self.assertTrue(settings[key])
        self.assertEqual(settings["MultipleInstances"], "IgnoreNew")
        self.assertEqual(settings["ExecutionTimeLimit"], 0)
        self.assertEqual(settings["RestartCount"], 2)
        self.assertEqual(settings["RestartInterval"], 60)

    def test_ilk_gunluk_tetikleme_gecmise_kurulmaz(self):
        response = self.invoke(r'''
$before = Get-Date
$null = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur -Saat 00:00
$result = @{ future = ([datetime]$global:UfukTask.Triggers[0].StartBoundary -gt $before) }
''')
        self.assertTrue(response["ok"], response.get("error"))
        self.assertTrue(response["result"]["future"])

    def test_ayni_kurulum_saati_guncellenir_ve_durum_gercek_gorevi_okur(self):
        response = self.invoke(r'''
$null = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur -Saat 08:00
$null = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur -Saat 09:30
$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Durum
''')
        self.assertTrue(response["ok"], response.get("error"))
        self.assertTrue(self.calls(response, "register")[1]["Force"])
        result = response["result"]
        self.assertTrue(result["owned"])
        self.assertEqual(result["last_task_result"], 0)
        self.assertEqual(result["missed_runs"], 1)
        self.assertIn("T09:30:00", result["triggers"][0]["start_boundary"])

    def test_windows_yerel_hesap_adi_ve_aciklama_satir_sonu_kimligi_korur(self):
        response = self.invoke(r'''
# Yalıtılmış test ortamı COMPUTERNAME değişkenini taşımayabilir.
$env:COMPUTERNAME = [Environment]::MachineName
$global:UfukShortUserName = $true
$null = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur
$global:UfukTask.Description = $global:UfukTask.Description.Replace("`n", "`r`n")
$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Durum
''')
        self.assertTrue(response["ok"], response.get("error"))
        self.assertTrue(response["result"]["owned"])

    def test_kaldirma_yalniz_dogrulanmis_gorevi_hedefler_uygulamayi_durdurmaz(self):
        response = self.invoke(r'''
$null = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur
$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kaldir
''')
        self.assertTrue(response["ok"], response.get("error"))
        removed = self.calls(response, "unregister")
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["InputObject"]["TaskName"], "UFUK Günlük Takip")
        self.assertFalse(removed[0]["Confirm"])
        self.assertFalse(response["result"]["application_stopped"])
        self.assertFalse(response["result"]["files_deleted"])

    def test_olmayan_gorevi_kaldirma_baska_gorevlere_dokunmaz(self):
        response = self.invoke("$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kaldir")
        self.assertTrue(response["ok"], response.get("error"))
        self.assertFalse(response["result"]["existed"])
        self.assertEqual(self.calls(response, "unregister"), [])

    def test_ayni_adli_farkli_eylem_kullanici_veya_kimlik_degistirilmez(self):
        for mutation in ("$global:UfukTask.Actions[0].Arguments += ' -Unexpected'",
                         "$global:UfukTask.Principal.UserId = 'S-1-5-18'",
                         "$global:UfukTask.Principal.RunLevel = 'Highest'",
                         "$global:UfukTask.Principal.LogonType = 'Password'",
                         "$global:UfukTask.Description = 'Baska uygulama'",
                         "$global:UfukTask.Actions[0].WorkingDirectory += '-baska'"):
            for operation in ("Kur", "Kaldir"):
                with self.subTest(mutation=mutation, operation=operation):
                    response = self.invoke("$null = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur\n" + mutation +
                                           "\n$global:UfukCalls.Clear()\n$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem " + operation)
                    self.assertFalse(response["ok"])
                    self.assertIn("doğrulanamadı", response["error"])
                    self.assertEqual(self.calls(response, "register") + self.calls(response, "unregister"), [])

    def test_yabanci_gorev_durumu_salt_okunur_acik_uyari_verir(self):
        response = self.invoke(r'''
$null = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur
$global:UfukTask.Description = 'Baska uygulama'
$global:UfukCalls.Clear()
$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Durum
''')
        self.assertTrue(response["ok"], response.get("error"))
        self.assertTrue(response["result"]["installed"])
        self.assertFalse(response["result"]["owned"])
        self.assertEqual([call["name"] for call in response["calls"]], ["get"])

    def test_saat_dogrulamasi_kayittan_once_hata_verir(self):
        for value in ("24:00", "08:60", "8:00", "08:00;whoami"):
            with self.subTest(value=value):
                response = self.invoke("$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur -Saat '" + value + "'")
                self.assertFalse(response["ok"])
                self.assertEqual(response["calls"], [])

    def test_erisim_hatasi_yok_gorev_sayilmaz_ve_kurulum_denemez(self):
        response = self.invoke("$global:UfukQueryError = $true\n$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur")
        self.assertFalse(response["ok"])
        self.assertIn("erişilemedi", response["error"])
        self.assertEqual(self.calls(response, "register"), [])

    def test_windows_gorev_bulunamadi_hatasi_kurulu_degil_olarak_gosterilir(self):
        response = self.invoke("$global:UfukQueryMissing = $true\n$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Durum")
        self.assertTrue(response["ok"], response.get("error"))
        self.assertFalse(response["result"]["installed"])
        self.assertEqual(self.calls(response, "register"), [])

    def test_kurulum_reddi_yonetici_veya_parola_ile_yeniden_denenmez(self):
        response = self.invoke("$global:UfukRegisterError = $true\n$result = & $env:UFUK_AUTOMATIC_SCRIPT -Islem Kur")
        self.assertFalse(response["ok"])
        self.assertIn("Yönetici yetkisi veya parola kullanılmadı", response["error"])
        self.assertEqual(len(self.calls(response, "register")), 1)


if __name__ == "__main__":
    unittest.main()
