"""Preparar instrucciones de Projects a partir del catálogo canónico de materias.

No crea Projects en ChatGPT, issues, directorios de usuario ni datos académicos.
Salida Markdown de solo lectura, lista para copiar en ajustes de un Project.
"""
import argparse
from pathlib import Path
import re
import sys

from academic_access.core import AccessError, subjects
from academic_config import paths
from canvas_sync.common import SyncError

TEMPLATES = Path(__file__).resolve().parent / "templates"
VALID_ID = re.compile(r"MAT-[A-Z0-9-]{1,40}\Z")


def render(role, *, subject_id=None, runtime=None):
    if role == "coordinador":
        if subject_id is not None:
            raise ValueError("El Coordinador no se limita a una materia.")
        return (TEMPLATES / "coordinador.md").read_text(encoding="utf-8")
    if role != "materia":
        raise ValueError("Rol desconocido.")
    if not isinstance(subject_id, str) or not VALID_ID.fullmatch(subject_id):
        raise ValueError("Indique un materia_id exacto y válido, por ejemplo MAT-SER.")
    if runtime is None:
        runtime = Path(paths.runtime(env=("ACADEMIC_MANAGER_DATA",), default=paths.DEFAULT_RUNTIME))
    catalog = subjects(runtime)["materias"]  # Lee Excel/index sin crear archivos.
    match = next((x for x in catalog if x["materia_id"] == subject_id), None)
    if match is None:
        raise ValueError("La materia no está registrada en el Excel canónico.")
    return _subject(match)


def _subject(match):
    subject_id = match["materia_id"]
    title = match["nombre"]
    term = match["semestre_id"]
    if (not isinstance(title, str) or len(title) > 150 or
            not re.fullmatch(r"20\d{2}-[1-3]", str(term))):  # Como el registro de semestres.
        raise ValueError("Identidad de la materia inválida en el Excel.")
    raw = match.get("bloques")
    blocks = " y ".join(str(x) for x in raw) if isinstance(raw, list) and raw else "por confirmar"
    replacements = {"{{MATERIA_ID}}": subject_id, "{{MATERIA_NOMBRE}}": title,
                    "{{SEMESTRE}}": str(term), "{{BLOQUES}}": blocks}
    template = (TEMPLATES / "materia.md").read_text(encoding="utf-8")
    for key, value in replacements.items():
        template = template.replace(key, value)
    if "{{" in template or "}}" in template:
        raise ValueError("La plantilla contiene marcadores sin resolver.")
    return template


def render_all(runtime):
    """Coordinador y todas las materias del semestre activo, por nombre de archivo."""
    active = [x for x in subjects(runtime)["materias"] if x.get("semestre_activo") is True]
    if not active:
        raise ValueError("No hay materias del semestre activo en la base canónica.")
    files = {"coordinador.md": render("coordinador")}
    for match in active:
        if not isinstance(match["materia_id"], str) or not VALID_ID.fullmatch(match["materia_id"]):
            raise ValueError("Identificador de materia inválido en el Excel.")
        files[f"materia-{match['materia_id']}.md"] = _subject(match)
    return files


def write_all(files, folder):
    """Todo se genera antes de escribir: un fallo no deja una mezcla de versiones."""
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name in sorted(files):
        path = folder / name
        path.write_text(files[name], encoding="utf-8")
        written.append(path)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description="Instrucciones de futuros Projects de Academic Manager")
    parser.add_argument("role", choices=("coordinador", "materia", "all"),
                        help="all: escribe un archivo por Project (Coordinador y materias activas)")
    parser.add_argument("--materia", default=None, help="ID interno exacto para rol materia")
    parser.add_argument("--out", default=None,
                        help="Carpeta de salida de all (por defecto RUNTIME\\temporales\\instrucciones-projects)")
    parser.add_argument("--runtime", default=None,
                        help="Carpeta de datos (por defecto ACADEMIC_MANAGER_DATA o la del archivo de configuración del usuario, "
                             "o C:\\AcademicManagerData)")
    args = parser.parse_args(argv)
    try:
        args.runtime = paths.runtime(args.runtime, env=("ACADEMIC_MANAGER_DATA",),
                                     default=paths.DEFAULT_RUNTIME)
        if args.role == "all":
            if args.materia is not None:
                raise ValueError("all genera todas las materias activas; no admite --materia.")
            folder = (Path(args.out) if args.out is not None
                      else Path(args.runtime) / "temporales" / "instrucciones-projects")
            for path in write_all(render_all(Path(args.runtime)), folder):
                print(path)
            return 0
        if args.out is not None:
            raise ValueError("--out solo vale con all.")
        markdown = render(args.role, subject_id=args.materia, runtime=Path(args.runtime))
        sys.stdout.write(markdown)
        return 0
    except (AccessError, SyncError, OSError, ValueError) as exc:
        print(f"NO_GENERADO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
