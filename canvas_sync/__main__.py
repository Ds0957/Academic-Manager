"""Canvas GET-only y publicación académica controlada por Drive/Excel."""
import argparse
import json
import sys

from academic_config import paths
from academic_store.store import DB_NAME
from .common import SyncError, runtime_root
from capture.models import CaptureError
from .auth import authorize, status
from .sync import list_courses, sync
from .enriched import sync_rich
from .resources import retrieve_pdf
from .study import sync_study
from .reconcile import preview
from .routine import routine
from .workbook_blocks import publish as publish_blocks, refresh_from_published
from .workbook_enrollment import publish_enrollment
from .classification import (load_decisions, classify, approve as approve_assignment,
                             persist_rule_decisions)
from .workbook_activities import _load_snapshots, plan_activities, publish_activities
from .semester import (load_registry, active_mappings, list_canvas_courses,
                       discovery, save_discovery, onboard, set_active_block, set_course_blocks)


def main(argv=None):
    p = argparse.ArgumentParser(description="Canvas Sync: consulta GET y sincronización segura con Excel")
    p.add_argument("action", choices=("login", "status", "courses", "sync", "sync-rich", "sync-study", "pdf", "renew", "reconcile-preview", "daily", "courses-review", "onboard", "block-set", "course-blocks", "blocks-preview", "blocks-publish", "blocks-refresh", "enroll-preview", "enroll-publish", "activities-review", "activities-preview"))
    p.add_argument("--runtime", default=None,
                   help="Carpeta de datos (por defecto ACADEMIC_MANAGER_DATA o la del archivo de configuración del usuario; "
                        "en Windows C:\\AcademicManagerData)")
    p.add_argument("--course-id", type=int, default=82862)
    p.add_argument("--file-id", type=int, default=None,
                   help="ID Canvas de PDF incluido en el catálogo v01 del curso")
    p.add_argument("--approved-only", action="store_true",
                   help="Con sync-study --all-active, limitarse a matrículas confirmadas")
    p.add_argument("--full-refresh", action="store_true",
                   help="Con sync-study, revalidar todos los PDF en el servidor institucional")
    p.add_argument("--all-active", action="store_true",
                   help="Con sync-rich o sync-study, consultar los cursos activos de esta cuenta")
    p.add_argument("--offline", action="store_true", help="Con daily, usar snapshots locales sin consultar Canvas")
    p.add_argument("--apply", action="store_true", help="Con daily, publicar solo actividades/cambios verificables por protocolo Drive")
    p.add_argument("--semester", default=None, help="Para onboard: código del semestre YYYY-N")
    p.add_argument("--block", type=int, choices=(1, 2), help="Para block-set: bloque activo 1 o 2")
    args = p.parse_args(argv)
    try:
        args.runtime = paths.runtime(args.runtime, env=("ACADEMIC_MANAGER_DATA",),
                                     default=paths.windows_default())
        if not args.runtime:
            raise SyncError("Indique runtime local con --runtime.")
        runtime = runtime_root(args.runtime)
        if args.action == "login":
            authorize(runtime)
        elif args.action == "renew":
            authorize(runtime, renew=True)
        elif args.action == "status":
            return 0 if status(runtime) else 1
        elif args.action == "courses":
            list_courses(runtime)
        elif args.action == "sync-rich":
            sync_rich(runtime, course_id=args.course_id, all_active=args.all_active)
        elif args.action == "sync-study":
            sync_study(runtime, course_id=args.course_id, all_active=args.all_active,
                       full_refresh=args.full_refresh, approved_only=args.approved_only)
        elif args.action == "courses-review":
            registry = load_registry(runtime)
            discovered = save_discovery(runtime, registry, list_canvas_courses(runtime))
            print(f"Semestre activo: {registry['active_semester']}; bloque: {registry['active_block']}; "
                  f"cursos asociados visibles: {len(discovered['active_known'])}; "
                  f"cursos sin asociar: {len(discovered['candidates'])}.")
            for entry in discovered["candidates"]:
                print(f"  SIN ASOCIAR {entry['course_id']}: {entry['name']}")
        elif args.action == "onboard":
            if not sys.stdin.isatty():
                raise SyncError("El alta de materias requiere una terminal interactiva local.")
            if not args.semester:
                raise SyncError("Indique --semester YYYY-N para incorporar cursos.")
            registry = load_registry(runtime)
            available = list_canvas_courses(runtime)
            candidates = discovery(registry, available)["candidates"]
            if not candidates:
                print("No hay cursos nuevos para incorporar.")
                return 0
            print(f"Alta del semestre {args.semester}. Cursos sin asociar:")
            for entry in candidates:
                print(f"  {entry['course_id']}: {entry['name']}")
            chosen = input("IDs de cursos a incorporar (separados por coma; vacío cancela): ").strip()
            if not chosen:
                print("Cancelado sin cambios.")
                return 0
            try:
                ids = [int(t.strip()) for t in chosen.split(",")]
            except ValueError:
                raise SyncError("IDs inválidos; no se guardó nada.")
            blocks = {}
            for cid in ids:
                raw = input(f"Curso {cid}: bloques (1, 2 o 1,2; vacío cancela): ").strip()
                if not raw:
                    print("Cancelado sin cambios.")
                    return 0
                try:
                    blocks[cid] = [int(x.strip()) for x in raw.split(",")]
                except ValueError:
                    raise SyncError("Bloques inválidos; no se guardó nada.")
            print("La asociación es LOCAL y queda pendiente de reflejarse en el Excel canónico.")
            if input(f"Para activar {args.semester} con {len(ids)} cursos, escriba CONFIRMAR: ").strip() != "CONFIRMAR":
                print("Cancelado sin cambios.")
                return 0
            added = onboard(runtime, args.semester, ids, available,
                            course_blocks=blocks, confirm=True)
            print(f"{len(added)} cursos incorporados al registro local; Excel sin modificar.")
            try:
                preview_result = publish_enrollment(runtime, load_registry(runtime), dry_run=True)
            except SyncError as exc:
                print("El alta queda pendiente de publicación segura:", exc)
            else:
                print(f"Previsualización canónica: {len(preview_result['changes'])} cambios de celda.")
                if (preview_result["changes"] and
                        input("Escriba PUBLICAR para confirmar una sola vez en Excel/Drive: ").strip() == "PUBLICAR"):
                    output = publish_enrollment(runtime, load_registry(runtime), dry_run=False)
                    print(f"Excel publicado y revalidado: SHA {output['sha256']}.")
                else:
                    print("El alta quedó local y pendiente; puede retomarse con enroll-publish.")
        elif args.action == "course-blocks":
            if not sys.stdin.isatty():
                raise SyncError("La revisión de bloques requiere una terminal interactiva.")
            registry = load_registry(runtime)
            print(f"Materias del semestre {registry['active_semester']}:")
            for course in registry["courses"]:
                if course["semester"] == registry["active_semester"]:
                    print(f"  {course['course_id']} / {course['subject_id']}: {course['blocks']}")
            raw_id = input("ID Canvas de la materia (vacío cancela): ").strip()
            if not raw_id:
                print("Cancelado sin cambios.")
                return 0
            try:
                cid = int(raw_id)
                blocks = [int(v.strip()) for v in input("Bloques nuevos: 1, 2 o 1,2: ").split(",")]
            except ValueError:
                raise SyncError("ID o bloques inválidos.")
            if input("Escriba CONFIRMAR para cambiar la pertenencia: ").strip() != "CONFIRMAR":
                print("Cancelado sin cambios.")
                return 0
            changed = set_course_blocks(runtime, cid, blocks, confirm=True)
            print("Pertenencia actualizada." if changed else "La materia ya estaba vinculada a esos bloques.")
        elif args.action == "block-set":
            if not sys.stdin.isatty():
                raise SyncError("Cambiar de bloque requiere una terminal interactiva.")
            registry = load_registry(runtime)
            if args.block is None:
                raise SyncError("Indique --block 1 o --block 2.")
            if input(f"Cambiar el semestre {registry['active_semester']} al bloque {args.block}? "
                     "Escriba CONFIRMAR: ").strip() != "CONFIRMAR":
                print("Cancelado sin cambios.")
                return 0
            changed = set_active_block(runtime, args.block, confirm=True)
            print("Bloque actualizado." if changed else "Ese bloque ya estaba activo.")
        elif args.action == "enroll-preview":
            current = load_registry(runtime)
            report = publish_enrollment(runtime, current, dry_run=True)
            print(f"Altas/semestres: {len(report['changes'])} celdas propuestas; Excel sin modificar.")
            for sheet, row, column, before, after in report["changes"]:
                print(f"  {sheet} ({row},{column}): {before!r} -> {after!r}")
        elif args.action == "enroll-publish":
            if not sys.stdin.isatty():
                raise SyncError("La publicación de nuevas matrículas requiere terminal interactiva.")
            current = load_registry(runtime)
            preview_result = publish_enrollment(runtime, current, dry_run=True)
            print(f"Propuesta: {len(preview_result['changes'])} cambios de celda en Excel.")
            if not preview_result["changes"]:
                print("No hay altas pendientes de publicar.")
                return 0
            for sheet, row, column, before, after in preview_result["changes"]:
                print(f"  {sheet} ({row},{column}): {before!r} -> {after!r}")
            if input("Escriba PUBLICAR para confirmar en Excel/Drive: ").strip() != "PUBLICAR":
                print("Cancelado sin modificar Excel.")
                return 0
            outcome = publish_enrollment(runtime, current, dry_run=False)
            print(f"Excel publicado y revalidado: SHA {outcome['sha256']}.")
        elif args.action == "blocks-preview":
            current = load_registry(runtime)
            result = publish_blocks(runtime, current, dry_run=True)
            print(f"Bloques: {len(result['changes'])} celdas propuestas; Drive sin cambios.")
            for sheet, row, column, old, new in result["changes"]:
                print(f"  {sheet} ({row},{column}): {old!r} -> {new!r}")
        elif args.action == "blocks-publish":
            if not sys.stdin.isatty():
                raise SyncError("Publicación canónica requiere confirmación interactiva.")
            current = load_registry(runtime)
            plan_result = publish_blocks(runtime, current, dry_run=True)
            print(f"Se proponen {len(plan_result['changes'])} cambios al XLSX compartido.")
            if not plan_result["changes"]:
                print("No hay cambios que publicar.")
                return 0
            if input("Escriba PUBLICAR para iniciar el protocolo compartido: ").strip() != "PUBLICAR":
                print("Cancelado sin escribir.")
                return 0
            outcome = publish_blocks(runtime, current, dry_run=False)
            print(f"Publicación verificada: {outcome['mode']}, SHA {outcome['sha256']}.")
        elif args.action == "blocks-refresh":
            data = refresh_from_published(runtime)
            print(f"Actualización verificada: {data['active_semester']} "
                  f"bloque {data['active_block']}, {data['courses']} materias.")
        elif args.action == "activities-review":
            if not sys.stdin.isatty():
                raise SyncError("Revisión de excepciones requiere una terminal interactiva.")
            registry = load_registry(runtime)
            snapshots = _load_snapshots(runtime, active_mappings(registry))
            entries, counts = classify(snapshots, load_decisions(runtime))
            pending = [x for key, x in entries.items() if x["category"] == "revisar"]
            print(f"Casos ambiguos: {len(pending)}; clasificaciones: {counts}.")
            for x in pending:
                key = f"{x['course_id']}:{x['assignment_id']}"
                row = x["row"]
                print(f"\n{key} | {row.get('name')} | puntos={row.get('points_possible')}"
                      f" | entrega={((row.get('submission') or {}).get('workflow_state'))}")
                answer = input("Decisión [a=actividad, r=referencia, n=resumen, Enter=posponer]: ").strip().lower()
                if not answer:
                    continue
                values = {"a":"actividad", "r":"referencia", "n":"resumen_calificaciones"}
                if answer not in values:
                    print("Opción inválida: se pospone.")
                    continue
                if input(f"Escriba CONFIRMAR para {values[answer]}: ").strip() == "CONFIRMAR":
                    approve_assignment(runtime, key, values[answer], snapshots, confirm=True)
                    print("Decisión guardada para próximas sincronizaciones.")
        elif args.action == "activities-preview":
            registry = load_registry(runtime)
            snapshots = _load_snapshots(runtime, active_mappings(registry))
            changes, exceptions = plan_activities(
                runtime / DB_NAME, registry, snapshots, load_decisions(runtime))
            print(f"Previsualización LOCAL: {len(changes)} celdas; "
                  f"{sum(x[2]==1 for x in changes)} actividades nuevas; "
                  f"{len(exceptions)} excepciones.")
            for x in exceptions:
                print("  REVISAR", x)
        elif args.action == "daily":
            if args.offline and args.apply:
                raise SyncError("--apply exige sincronización en vivo; no publicar snapshots antiguos.")
            outcome = routine(runtime, do_sync=not args.offline)
            if outcome.get("incomplete_coverage"):
                print("No se reconcilió: faltan cursos del semestre activo en Canvas.")
                print("IDs no visibles:", outcome["missing_course_ids"])
                for entry in (outcome.get("discovery") or {}).get("candidates", []):
                    print(f"  CURSO SIN ASOCIAR {entry['course_id']}: {entry['name']}")
                print("Revise con courses-review y, si corresponde, onboard --semester YYYY-N.")
                return 2
            summary = outcome["report"]["resumen"]
            print(f"Semestre {outcome['report']['semestre']}, bloque {outcome['report']['bloque']}.")
            print(f"Rutina Canvas: {summary['total_canvas']} registros; "
                  f"{summary['ya_en_base']} en V2, {summary['excluidos_de_actividades']} informativos/resumen, "
                  f"{summary['pendientes']} pendientes; {len(outcome['report']['novedades'])} novedades.")
            print("Informe local:", outcome["path"])
            notice = (outcome["report"].get("token") or {}).get("aviso")
            if notice:
                print("AVISO TOKEN CANVAS:", notice)
            for item in outcome["report"]["novedades"]:
                print(f"  REVISAR {item['key']}: {item['titulo']} ({item['novedad']})")
            discovered = outcome.get("discovery") or {}
            for candidate in discovered.get("candidates", []):
                print(f"  CURSO SIN ASOCIAR {candidate['course_id']}: {candidate['name']}")
            if args.apply:
                current = load_registry(runtime)
                observed = outcome.get("observed_course_ids") or list(active_mappings(current))
                snapshots = _load_snapshots(runtime, observed)
                decisions = load_decisions(runtime)
                dry = publish_activities(runtime, current, snapshots, decisions, dry_run=True)
                if dry["changes"]:
                    result = publish_activities(runtime, current, snapshots, decisions, dry_run=False)
                    print(f"ACTIVIDADES PUBLICADAS: {len(result['changes'])} celdas; "
                          f"SHA {result['sha256']}.")
                else:
                    print("Actividades: sin actualizaciones verificables.")
                saved = persist_rule_decisions(runtime, snapshots)
                aligned = routine(runtime, do_sync=False)
                pending = classify(snapshots, load_decisions(runtime))[1]["revisar"]
                print(f"Base alineada: {aligned['report']['resumen']['ya_en_base']} "
                      f"actividades vinculadas; {pending} casos ambiguos.")
                print(f"Clasificaciones persistidas nuevas: {saved}.")
                if pending:
                    print("Excepciones reales: ejecutar activities-review una sola vez.")
                else:
                    print("Clasificación: sin decisiones pendientes.")
        elif args.action == "reconcile-preview":
            mappings = active_mappings(load_registry(runtime))
            ids = tuple(mappings) if args.all_active else (args.course_id,)
            report = preview(runtime, course_ids=ids, mappings=mappings)
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        elif args.action == "pdf":
            if args.file_id is None:
                raise SyncError("Indique --file-id del PDF autorizado.")
            retrieve_pdf(runtime, args.course_id, args.file_id)
        else:
            if args.course_id <= 0:
                raise SyncError("ID de curso inválido.")
            sync(runtime, args.course_id)
        return 0
    except (SyncError, CaptureError, paths.ConfigError) as exc:
        print("Canvas Sync:", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
