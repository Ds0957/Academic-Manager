# Desinstalador de Academic Manager: quita la tarea diaria solo si TODAS sus acciones usan el Python de
# esta copia instalada (el -Python que pasa install-canvas-daily-task.ps1 desde el asistente). Una tarea
# que apunta a otra instalación (por ejemplo la de desarrollo) nunca se toca. Nunca bloquea la
# desinstalación: siempre termina con código 0.
param([string]$Python = "")
try {
    if (-not $Python) { exit 0 }
    $taskName = "AcademicManager-CanvasDaily"
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) { exit 0 }
    $expected = '-Python "' + $Python + '"'
    $ours = @($task.Actions | Where-Object {
        $_.Arguments -and $_.Arguments.IndexOf($expected, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
    if ($ours.Count -gt 0 -and $ours.Count -eq @($task.Actions).Count) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
} catch {
    # Sin permiso o sin el módulo de tareas: se deja la tarea como estaba.
}
exit 0
