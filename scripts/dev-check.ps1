param(
    [string]$Python = $env:ACADEMIC_MANAGER_PYTHON
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $Python) {
    $Python = "C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "No se encontró Python de Academic Manager: $Python"
}

$Git = $env:ACADEMIC_MANAGER_GIT
if (-not $Git) {
    $candidate = Get-Command git -ErrorAction SilentlyContinue
    if ($candidate) {
        $Git = $candidate.Source
    } elseif (Test-Path -LiteralPath "C:\AcademicManagerDev\tools\git\cmd\git.exe") {
        $Git = "C:\AcademicManagerDev\tools\git\cmd\git.exe"
    }
}

Write-Host "== Academic Manager dev check =="
Write-Host "Repo: $RepoRoot"
Write-Host "Python: $Python"

Push-Location $RepoRoot
try {
    & $Python -m compileall -q capture canvas_sync tests
    if ($LASTEXITCODE -ne 0) { throw "compileall falló." }

    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "La suite de tests falló." }

    if ($Git) {
        & $Git diff --check
        if ($LASTEXITCODE -ne 0) { throw "git diff --check detectó problemas." }
        & $Git status --short --branch
    } else {
        Write-Warning "Git no está disponible; se omitió diff/status."
    }

    Write-Host "DEV_CHECK_OK"
}
finally {
    Pop-Location
}
