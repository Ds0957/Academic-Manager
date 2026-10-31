# Academic Manager Canvas: rutina diaria con publicación segura, sin secretos.
# Sin parámetros usa la instalación de desarrollo de siempre (repo, Python y C:\AcademicManagerData).
# Una copia instalada pasa -Repo y -Python (install-canvas-daily-task.ps1 desde el asistente) y la
# carpeta de datos la decide entonces su config.json, preguntando a academic_config.paths; -Runtime
# la fija a mano.
param(
    [string]$Repo = "C:\AcademicManagerDev\Academic-Manager",
    [string]$Python = "C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe",
    [string]$Runtime = ""
)
$ErrorActionPreference = "Stop"
# PowerShell no distingue mayúsculas: $repo, $python y $runtime son los mismos parámetros.
if ($Runtime) {
    # -Runtime explícito: se usa tal cual.
} elseif ($PSBoundParameters.ContainsKey("Python") -or $PSBoundParameters.ContainsKey("Repo")) {
    # Copia instalada: la misma carpeta de datos que usan sus comandos (JSON ASCII, sin problemas de codificación).
    Set-Location $repo
    $env:PYTHONPATH = $repo
    $resolved = (& $python -c "import json; from academic_config import paths; print(json.dumps(paths.runtime(env=('ACADEMIC_MANAGER_DATA',), default=paths.windows_default())))" | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $resolved -or $resolved -eq "null") {
        throw "No se pudo leer la carpeta de datos de Academic Manager (config.json)."
    }
    $runtime = $resolved | ConvertFrom-Json
} else {
    $runtime = "C:\AcademicManagerData"
}
$logDir = Join-Path $runtime "procesamiento\canvas-sync\logs"
New-Item -Path $logDir -ItemType Directory -Force | Out-Null
$stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
$code = 1
$message = ""
try {
    Set-Location $repo
    $env:PYTHONPATH = $repo
    $message = (& $python -m canvas_sync daily --apply 2>&1 | Out-String)
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        $studyMessage = (& $python -m canvas_sync sync-study --all-active --approved-only 2>&1 | Out-String)
        $message += [Environment]::NewLine + "CANVAS_STUDY_INCREMENTAL:" + [Environment]::NewLine + $studyMessage
        $code = $LASTEXITCODE
    }
    if ($code -eq 0) {
        $knowledgeMessage = (& $python -m academic_knowledge build 2>&1 | Out-String)
        $message += [Environment]::NewLine + "ACADEMIC_KNOWLEDGE:" + [Environment]::NewLine + $knowledgeMessage
        $code = $LASTEXITCODE
    }
} catch {
    $message += [Environment]::NewLine + "Error operacional: " + $_.Exception.Message
}
$report = "[{0}] exit_code={1}`r`n{2}`r`n" -f $stamp,$code,$message
Add-Content -LiteralPath (Join-Path $logDir ("canvas-" + (Get-Date).ToString("yyyy-MM-dd") + ".log")) -Value $report -Encoding UTF8
$summary = @{timestamp=$stamp; exit_code=$code; message=$message.Trim()}
$summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $logDir "latest-status.json") -Encoding UTF8
exit $code
