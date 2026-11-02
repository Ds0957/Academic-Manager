"""Ayudante de construcción del instalador (lo llama installer/build.ps1; ver docs/39).

    python stage.py pth <carpeta-python>     activa site-packages y ..\\app en el Python portátil
    python stage.py app <repositorio> <destino>   copia solo el código que usa la app instalada

Solo biblioteca estándar: lo ejecuta el propio Python portátil antes de tener pip. Copia solo
archivos que git conoce (`git ls-files`): nada sin seguimiento del PC que construye (notas, secretos,
cachés) viaja en el instalador.
"""
import shutil
import subprocess
import sys
from pathlib import Path

# Paquetes que la app instalada importa; lo demás (tests, odd, docs, .git, scripts .cmd de la
# instalación de desarrollo, requisitos) no viaja en el instalador.
PACKAGES = ("academic_access", "academic_app", "academic_config", "academic_knowledge", "academic_mcp", "academic_operations",
            "academic_projects", "academic_setup", "academic_store", "canvas_sync", "capture")
SCRIPTS = ("canvas-daily.ps1", "install-canvas-daily-task.ps1")
# Rutas relativas a la carpeta del ._pth: su zip de biblioteca estándar, su carpeta, los paquetes
# instalados con pip y el código de Academic Manager ({app}\app junto a {app}\python).
PTH_LINES = (r"Lib\site-packages", r"..\app", "import site")


def write_pth(python_dir):
    found = sorted(Path(python_dir).glob("python3*._pth"))
    if len(found) != 1:
        raise SystemExit(f"Se esperaba un único python3*._pth en {python_dir}; hay {len(found)}.")
    pth = found[0]
    zip_name = pth.stem + ".zip"
    pth.write_text("\r\n".join((zip_name, ".", *PTH_LINES)) + "\r\n", encoding="utf-8", newline="")
    return pth


def tracked(repo, folder):
    """Archivos de `folder` que git sigue, relativos al repositorio (falla si no es un repositorio)."""
    result = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--", folder],
                            capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"git ls-files falló en {repo}: {result.stderr.decode(errors='replace').strip()}")
    return [name for name in result.stdout.decode("utf-8").split("\0") if name]


def copy_app(repo, out, packages=PACKAGES):
    repo, out = Path(repo), Path(out)
    if out.exists():
        raise SystemExit(f"La carpeta de destino ya existe: {out}. Use una carpeta nueva.")
    wanted = [*packages, *(f"scripts/{name}" for name in SCRIPTS)]
    for entry in wanted:
        files = tracked(repo, entry)
        if not files:
            raise SystemExit(f"{entry} no tiene archivos con seguimiento de git en {repo}.")
        for name in files:
            target = out / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / name, target)
    return out


def main(argv):
    if len(argv) == 2 and argv[0] == "pth":
        print(write_pth(argv[1]))
        return 0
    if len(argv) == 3 and argv[0] == "app":
        print(copy_app(argv[1], argv[2]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
