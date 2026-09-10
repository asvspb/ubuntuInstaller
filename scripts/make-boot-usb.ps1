<#
.SYNOPSIS
    make-boot-usb.ps1 — Автоматизированное создание загрузочных флешек Ventoy
                        для Windows (PowerShell) с авто-бэкапом данных.

.DESCRIPTION
    Скрипт интерактивно определяет подключенные USB-накопители, проверяет целостность,
    автоматически сохраняет существующие файлы и возвращает их после разметки.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# Включение современных протоколов TLS для безопасного скачивания с GitHub
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13

# Настройка UTF-8 для корректного отображения русского языка
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ------------------------------------------------------------------------------
# 0. Проверка прав Администратора
# ------------------------------------------------------------------------------
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "Скрипт требует прав Администратора. Перезапуск с повышенными привилегиями..."
    Start-Process powershell.exe -ArgumentList ("-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"") -Verb RunAs
    exit
}

Clear-Host
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "   🛠️ VENTOY BOOT USB BUILDER & AUTOMATOR (WINDOWS / POWERSHELL)      " -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

$backupDir = "$env:TEMP\usb_backup_$(Get-Random)"
$doRestore = $false

# ------------------------------------------------------------------------------
# Функция проверки целостности и битых файлов
# ------------------------------------------------------------------------------
function Test-DriveIntegrity {
    param([int]$diskNumber)
    Write-Host "`n======================================================================" -ForegroundColor Cyan
    Write-Host "  🩺 ПРОВЕРКА ЦЕЛОСТНОСТИ И БИТЫХ ФАЙЛОВ НА ФЛЕШКЕ:" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan

    $partitions = Get-Partition -DiskNumber $diskNumber -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter }
    if (-not $partitions) {
        Write-Host "[ИНФО] На накопителе нет смонтированных разделов для проверки файловой структуры." -ForegroundColor Yellow
        return
    }

    $hasErrors = $false
    foreach ($part in $partitions) {
        $letter = "$($part.DriveLetter):"
        Write-Host "`n• Сканирование раздела $letter..." -ForegroundColor Cyan
        try {
            $repairResult = Repair-Volume -DriveLetter $part.DriveLetter -Scan -ErrorAction SilentlyContinue
            if ($repairResult -eq "NoErrorsFound" -or $repairResult -eq 0 -or $null -eq $repairResult) {
                Write-Host "  ✅ Раздел ${letter} файловая структура исправна, битых файлов не обнаружено." -ForegroundColor Green
            } else {
                Write-Host "  ⚠️ Раздел ${letter} обнаружены ошибки ($repairResult). Будут исправлены при форматировании." -ForegroundColor Yellow
                $hasErrors = $true
            }
        }
        catch {
            Write-Host "  [ИНФО] Сканирование раздела $letter завершено." -ForegroundColor Gray
        }
    }

    if (-not $hasErrors) {
        Write-Host "`n✅ Целостность накопителя в норме. Ошибок не обнаружено." -ForegroundColor Green
    } else {
        Write-Host "`n⚠️ Обнаружены ошибки в текущих разделах. Форматирование полностью создаст чистую разметку." -ForegroundColor Yellow
    }
}

# ------------------------------------------------------------------------------
# Функция замера скорости чтения / записи
# ------------------------------------------------------------------------------
function Run-SpeedTest {
    param(
        [string]$driveLetter,
        [string]$title,
        [int]$sizeMB = 512
    )
    $path = "$($driveLetter):\"
    if (-not (Test-Path $path)) { return }
    $testFile = Join-Path $path "__speed_test_tmp.dat"
    Write-Host "`n⏱️ Замер скорости: $title (блок $sizeMB МБ)..." -ForegroundColor Cyan

    try {
        $buffer = New-Object byte[] (4MB)
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $rng.GetBytes($buffer)

        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $fs = [System.IO.File]::Create($testFile, 4MB, [System.IO.FileOptions]::WriteThrough)
        for ($b = 0; $b -lt ($sizeMB / 4); $b++) {
            $fs.Write($buffer, 0, $buffer.Length)
        }
        $fs.Flush($true)
        $fs.Close()
        $sw.Stop()
        $writeSec = $sw.Elapsed.TotalSeconds
        $writeSpeed = [math]::Round($sizeMB / $writeSec, 2)

        $sw.Restart()
        $fs = [System.IO.File]::OpenRead($testFile)
        $readBuf = New-Object byte[] (4MB)
        while (($read = $fs.Read($readBuf, 0, $readBuf.Length)) -gt 0) {}
        $fs.Close()
        $sw.Stop()
        $readSec = $sw.Elapsed.TotalSeconds
        $readSpeed = [math]::Round($sizeMB / $readSec, 2)

        Remove-Item $testFile -Force -ErrorAction SilentlyContinue

        Write-Host "  • ✍️  Запись : $writeSpeed МБ/с ($sizeMB МБ за $([math]::Round($writeSec, 2)) сек)" -ForegroundColor Green
        Write-Host "  • 📖  Чтение : $readSpeed МБ/с ($sizeMB МБ за $([math]::Round($readSec, 2)) сек)" -ForegroundColor Green
    }
    catch {
        Write-Host "  [ВНИМАНИЕ] Не удалось выполнить тест: $_" -ForegroundColor Yellow
    }
}

# ------------------------------------------------------------------------------
# 1. Поиск и выбор USB-накопителя
# ------------------------------------------------------------------------------
$usbDisks = Get-Disk | Where-Object { $_.BusType -eq 'USB' -and $_.OperationalStatus -eq 'Online' }

if (-not $usbDisks) {
    Write-Host "[ОШИБКА] Подключенные USB-накопители не найдены! Вставьте флешку и повторите запуск." -ForegroundColor Red
    Pause
    exit
}

Write-Host "`nДоступные USB-диски:" -ForegroundColor Yellow
$i = 1
foreach ($disk in $usbDisks) {
    $sizeGB = [math]::Round($disk.Size / 1GB, 2)
    Write-Host "  [$i] Диск $($disk.Number): $($disk.FriendlyName) ($sizeGB GB)" -ForegroundColor Green
    $i++
}

Write-Host ""
$sel = Read-Host "Выберите номер диска [1-$($usbDisks.Count)]"
$diskIndex = [int]$sel - 1

if ($diskIndex -lt 0 -or $diskIndex -ge $usbDisks.Count) {
    Write-Host "[ОШИБКА] Неверный выбор." -ForegroundColor Red
    exit
}

$targetDisk = $usbDisks[$diskIndex]
$totalSizeGB = [math]::Round($targetDisk.Size / 1GB, 2)
$totalSizeMB = [math]::Round($targetDisk.Size / 1MB, 0)

Write-Host "`nВыбран целевой накопитель: Диск $($targetDisk.Number) ($($targetDisk.FriendlyName), $totalSizeGB GB)" -ForegroundColor Cyan

# Автоматическая проверка целостности накопителя
Test-DriveIntegrity -diskNumber $targetDisk.Number

# Сканирование и бэкап существующих файлов
$existingParts = Get-Partition -DiskNumber $targetDisk.Number -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter }
$foundFiles = $false

if ($existingParts) {
    foreach ($p in $existingParts) {
        $pPath = "$($p.DriveLetter):\"
        if (Test-Path $pPath) {
            $items = Get-ChildItem -Path $pPath -Exclude '$RECYCLE.BIN','System Volume Information' -Force -ErrorAction SilentlyContinue
            if ($items) { $foundFiles = $true; break }
        }
    }
}

if ($foundFiles) {
    Write-Host "`n======================================================================" -ForegroundColor Cyan
    Write-Host "  💾 ОБНАРУЖЕНЫ ДАННЫЕ НА НАКОПИТЕЛЕ:" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    $ansB = Read-Host "Сохранить существующие файлы и вернуть их после переразметки? [Y/n]"
    if ([string]::IsNullOrWhiteSpace($ansB) -or $ansB -match '^[YyДд]') {
        $doRestore = $true
        New-Item -Path "$backupDir\iso" -ItemType Directory -Force | Out-Null
        New-Item -Path "$backupDir\data" -ItemType Directory -Force | Out-Null
        Write-Host "Копирование файлов во временное хранилище на ПК..." -ForegroundColor Cyan

        foreach ($p in $existingParts) {
            $pPath = "$($p.DriveLetter):\"
            Get-ChildItem -Path $pPath -Exclude '$RECYCLE.BIN','System Volume Information' -Force -ErrorAction SilentlyContinue | ForEach-Object {
                if ($_.Extension -match '^\.(iso|img|vhd|wim)$') {
                    Copy-Item -Path $_.FullName -Destination "$backupDir\iso\" -Recurse -Force
                } else {
                    Copy-Item -Path $_.FullName -Destination "$backupDir\data\" -Recurse -Force
                }
            }
        }
        Write-Host "Резервная копия успешно создана на ПК." -ForegroundColor Green
    }
}

# ------------------------------------------------------------------------------
# 2. Настройка параметров: размер, ФС и метки разделов
# ------------------------------------------------------------------------------
Write-Host "`n======================================================================" -ForegroundColor Cyan
Write-Host "  📐 НАСТРОЙКА РАЗДЕЛОВ НАКОПИТЕЛЯ (Всего доступно: $totalSizeGB GB):" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

# 1. Ввод размера раздела Ventoy
$rawSize = Read-Host "Размер раздела 1 под Ventoy / ISO в ГБ [по умолчанию: 8] (или 'all' на весь диск)"
if ([string]::IsNullOrWhiteSpace($rawSize)) { $rawSize = "8" }

if ($rawSize -eq "all" -or [double]$rawSize -ge $totalSizeGB) {
    $ventoySizeGB = $totalSizeGB
    $createDataPart = $false
    Write-Host "[ИНФО] Будет создан единый раздел на весь диск ($totalSizeGB GB)." -ForegroundColor Yellow
} else {
    $ventoySizeGB = [double]$rawSize
    $createDataPart = $true
    $remainGB = [math]::Round($totalSizeGB - $ventoySizeGB, 2)

    # 2. Выбор файловой системы для второго раздела (раздел данных)
    Write-Host "`n💾 Выбор файловой системы для раздела данных (~$remainGB GB):" -ForegroundColor Yellow
    Write-Host "  [1] 🌐 exFAT (по умолчанию — совместим с Android, Windows, Mac, Linux)" -ForegroundColor Green
    Write-Host "  [2] 🪟 NTFS (для Windows)" -ForegroundColor White
    Write-Host "  [3] 📦 FAT32 (для старых систем и ТВ)" -ForegroundColor White
    $fsChoice = Read-Host "Выберите ФС [1-3, по умолчанию: 1]"
    if ([string]::IsNullOrWhiteSpace($fsChoice)) { $fsChoice = "1" }

    switch ($fsChoice) {
        "1" { $dataFS = "exFAT" }
        "2" { $dataFS = "NTFS" }
        "3" { $dataFS = "FAT32" }
        default { $dataFS = "exFAT" }
    }
}

# 3. Настройка меток разделов с наследованием индекса
Write-Host "`n🏷️ Настройка названий (меток) разделов:" -ForegroundColor Yellow
Write-Host "  • Формат раздела 1 (Ventoy) : FD-NN (например: FD-0, FD-1, FD-2 или просто номер '2')" -ForegroundColor Gray
Write-Host "  • Формат раздела 3 (Данные) : DATA-NN или DATA-<имя> (наследует индекс раздела 1)" -ForegroundColor Gray
Write-Host ""

$rawP1 = Read-Host "Метка раздела 1 (Ventoy / ISO) [по умолчанию: FD-0]"
if ([string]::IsNullOrWhiteSpace($rawP1)) { 
    $labelP1 = "FD-0" 
} elseif ($rawP1 -match '^\d+$') {
    $labelP1 = "FD-$rawP1"
} else {
    $labelP1 = $rawP1
}

if ($createDataPart) {
    $defP3 = "DATA"
    if ($labelP1 -match '^FD-(.+)$') {
        $defP3 = "DATA-$($Matches[1])"
    }

    $rawP3 = Read-Host "Метка раздела 3 (раздел данных) [по умолчанию: $defP3]"
    if ([string]::IsNullOrWhiteSpace($rawP3)) { 
        $labelP3 = $defP3 
    } elseif ($rawP3 -match '^\d+$') {
        $labelP3 = "DATA-$rawP3"
    } else {
        $labelP3 = $rawP3
    }
} else {
    $labelP3 = ""
}

# ------------------------------------------------------------------------------
# 3. Подтверждение и запуск установки
# ------------------------------------------------------------------------------
Write-Host "`n======================================================================" -ForegroundColor Red
Write-Host "  ⚠️ ВНИМАНИЕ: ВСЕ ДАННЫЕ НА ДИСКЕ $($targetDisk.Number) БУДУТ ПЕРЕРАЗМЕЧЕНЫ!" -ForegroundColor Red
Write-Host "======================================================================" -ForegroundColor Red
Write-Host " Параметры:"
Write-Host " • Диск:              $($targetDisk.Number) ($($targetDisk.FriendlyName), $totalSizeGB GB)"
Write-Host " • Раздел 1 (Ventoy): $ventoySizeGB GB (exFAT, Метка: $labelP1)"
Write-Host " • Раздел данных:     $([math]::Round($totalSizeGB - $ventoySizeGB, 2)) GB ($dataFS, Метка: $labelP3)"
Write-Host " • Авто-бэкап файлов: $(if ($doRestore) { 'Включен (данные будут восстановлены)' } else { 'Отключен' })"
Write-Host " • Таблица разделов:  GPT + UEFI Secure Boot"
Write-Host "----------------------------------------------------------------------"

$confirm = Read-Host "Для запуска форматирования введите ДА"
if ($confirm -ne "ДА") {
    Write-Host "Операция отменена пользователем." -ForegroundColor Yellow
    exit
}

# ------------------------------------------------------------------------------
# 4. Скачивание Ventoy для Windows (если отсутствует)
# ------------------------------------------------------------------------------
$ventoyVersion = "1.1.17"
$ventoyDir = "$env:TEMP\ventoy-$ventoyVersion"

if (-not (Test-Path "$ventoyDir\Ventoy2Disk.exe")) {
    Write-Host "`nСкачивание официального установщика Ventoy v$ventoyVersion..." -ForegroundColor Cyan
    $zipUrl = "https://github.com/ventoy/Ventoy/releases/download/v$ventoyVersion/ventoy-$ventoyVersion-windows.zip"
    $zipPath = "$env:TEMP\ventoy-$ventoyVersion.zip"
    
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath "$env:TEMP" -Force
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
}

# ------------------------------------------------------------------------------
# 5. Установка Ventoy на целевой USB диск
# ------------------------------------------------------------------------------
$reserveMB = 0
if ($createDataPart) {
    $reserveMB = [math]::Max(0, [int]($totalSizeMB - ($ventoySizeGB * 1024) - 64))
}

Write-Host "`nУстановка загрузчика Ventoy..." -ForegroundColor Cyan

$v2dCmd = "$ventoyDir\Ventoy2Disk.exe"

if ($reserveMB -gt 0) {
    $xmlContent = @"
<?xml version="1.0" encoding="utf-8"?>
<Ventoy2Disk>
    <DiskOption>
        <PartitionStyle>1</PartitionStyle>
        <ReserveSize>$reserveMB</ReserveSize>
    </DiskOption>
</Ventoy2Disk>
"@
    Set-Content -Path "$ventoyDir\ventoy_install.xml" -Value $xmlContent
}

# Запуск установки
Start-Process -FilePath $v2dCmd -ArgumentList "/dev/PhysicalDrive$($targetDisk.Number)" -Wait

Start-Sleep -Seconds 3
Update-HostStorageCache

# ------------------------------------------------------------------------------
# 6. Настройка меток и раздела данных
# ------------------------------------------------------------------------------
$partitions = Get-Partition -DiskNumber $targetDisk.Number
$vPart = $partitions | Where-Object { $_.PartitionNumber -eq 1 }
$efiPart = $partitions | Where-Object { $_.PartitionNumber -eq 2 }

# Установка метки первого раздела
if ($vPart) {
    $vol = Get-Volume -Partition $vPart -ErrorAction SilentlyContinue
    if ($vol) { Set-Volume -Partition $vPart -NewFileSystemLabel $labelP1 }
}

# Скрытие EFI раздела в Проводнике Windows
if ($efiPart) {
    Remove-PartitionAccessPath -DiskNumber $targetDisk.Number -PartitionNumber 2 -AccessPath "$($efiPart.DriveLetter):\" -ErrorAction SilentlyContinue
}

# Создание и форматирование раздела данных на свободном месте
if ($createDataPart) {
    Write-Host "Создание и форматирование раздела данных ($labelP3, $dataFS)..." -ForegroundColor Cyan
    $newPart = New-Partition -DiskNumber $targetDisk.Number -UseMaximumSize -AssignDriveLetter -ErrorAction SilentlyContinue
    if ($newPart) {
        Format-Volume -Partition $newPart -FileSystem $dataFS -NewFileSystemLabel $labelP3 -Confirm:$false
    }
}

# ------------------------------------------------------------------------------
# 7. Восстановление сохраненных файлов
# ------------------------------------------------------------------------------
if ($doRestore -and (Test-Path $backupDir)) {
    Write-Host "`nВосстановление сохраненных файлов на флешку..." -ForegroundColor Cyan
    $updatedParts = Get-Partition -DiskNumber $targetDisk.Number -ErrorAction SilentlyContinue
    $p1 = $updatedParts | Where-Object { $_.PartitionNumber -eq 1 }
    $p3 = $updatedParts | Where-Object { $_.PartitionNumber -eq 3 }

    # ISO в раздел 1
    if ($p1 -and $p1.DriveLetter -and (Test-Path "$backupDir\iso")) {
        Copy-Item -Path "$backupDir\iso\*" -Destination "$($p1.DriveLetter):\" -Recurse -Force -ErrorAction SilentlyContinue
    }
    # Данные в раздел 3
    if ($p3 -and $p3.DriveLetter -and (Test-Path "$backupDir\data")) {
        Copy-Item -Path "$backupDir\data\*" -Destination "$($p3.DriveLetter):\" -Recurse -Force -ErrorAction SilentlyContinue
    }

    Remove-Item $backupDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "🎉 Все сохраненные файлы успешно возвращены на накопитель!" -ForegroundColor Green
}

Write-Host "`n======================================================================" -ForegroundColor Green
Write-Host " 🎉 ФЛЕШКА УСПЕШНО РАЗМЕЧЕНА!" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Green

# Контрольное тестирование после создания
$doPostTest = Read-Host "`nПровести контрольный замер скорости на созданных разделах? [Y/n]"
if ([string]::IsNullOrWhiteSpace($doPostTest) -or $doPostTest -match '^[YyДд]') {
    $updatedParts = Get-Partition -DiskNumber $targetDisk.Number -ErrorAction SilentlyContinue
    $p1 = $updatedParts | Where-Object { $_.PartitionNumber -eq 1 }
    $p3 = $updatedParts | Where-Object { $_.PartitionNumber -eq 3 }
    
    if ($p1 -and $p1.DriveLetter) {
        Run-SpeedTest -driveLetter ($p1.DriveLetter) -title "Раздел 1: Ventoy / ISO ($labelP1)"
    }
    if ($p3 -and $p3.DriveLetter) {
        Run-SpeedTest -driveLetter ($p3.DriveLetter) -title "Раздел 3: Данные ($labelP3, $dataFS)"
    }
    Write-Host "`nВсе замеры скорости успешно завершены!" -ForegroundColor Green
}

Pause
