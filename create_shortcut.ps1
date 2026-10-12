# Creates (or refreshes) the desktop shortcut that opens the app in its own window.
# Called by setup.bat. Saved as UTF-8 with BOM so Windows PowerShell 5.1 reads the Hebrew name.
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found: $pythonw (run uv sync first)" }

$name = 'מערכת שאלות ותשובות.lnk'
$shell = New-Object -ComObject WScript.Shell

# Desktop (also handles a redirected desktop) + Start menu (found by Windows search, can be pinned).
foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $link = Join-Path $folder $name
    $shortcut = $shell.CreateShortcut($link)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = '-m app.desktop'
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = (Join-Path $root 'app\assets\app.ico') + ',0'
    $shortcut.Description = 'מערכת שאלות ותשובות — תיקי מטופלים'
    $shortcut.Save()
    Write-Host "Shortcut created: $link"
}
