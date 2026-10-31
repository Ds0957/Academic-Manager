"""CLI: python -m capture --help."""
import argparse
import math
import os
import re
from pathlib import Path
import sys
import time
from uuid import uuid4

from academic_config import paths

from .config import Config
from .models import CaptureError, now
from .sources import drive, local, auth, youtube
from .media.probe import probe
from .media.audio import extract
from .transcription.formatter import import_transcript, normalize, markdown
from .transcription.whisperx_engine import transcribe
from .storage.metadata import atomic_json, read_json, sha256
from .storage.paths import job_paths, within, clean_media
from .storage.job import job_lock, save
from .storage.publisher import validate, publish
from .analysis.analyzer import pending
from .analysis.reviewed import attach, recover
from .storage.monitor import record_event, print_status
from .storage.progress import ProgressReporter


MODELS = ("large-v3", "medium", "small")
DEFAULT_MODEL = "large-v3"


def parser():
    p = argparse.ArgumentParser(description="Academic Manager Capture V0")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--login-drive", action="store_true", help="Autorizar su cuenta Drive en el navegador")
    mode.add_argument("--source", help="Archivo local (C:/ruta/video.mp4), URL de un video de YouTube, "
                                        "o URL/ID de Google Drive")
    mode.add_argument("--transcript", help="Importar JSON segments o texto/Markdown existente")
    mode.add_argument("--resume", help="ID de job recuperable")
    mode.add_argument("--status", metavar="JOB_ID", help="Consultar job, latest o all sin procesar archivos")
    p.add_argument("--watch", type=int, help="Con --status, actualizar el contador cada N segundos (5 a 300)")
    p.add_argument("--runtime", help="Carpeta de datos (por defecto ACADEMIC_MANAGER_RUNTIME o ACADEMIC_MANAGER_DATA, luego el archivo "
                                     "de configuración del usuario; en Windows C:\\AcademicManagerData)")
    p.add_argument("--semester")
    p.add_argument("--subject")
    p.add_argument("--recording-name", help="Nombre original verificado al importar una transcripción")
    p.add_argument("--class-date", help="Fecha verificada YYYY-MM-DD; nunca se infiere")
    p.add_argument("--language", default="es",
                   help="Idioma del audio y de los subtítulos manuales de YouTube (por defecto es)")
    p.add_argument("--tipo", choices=("clase", "material"), default="clase",
                   help="clase (por defecto) o material de estudio: sin fecha ni ID CLS, se publica en "
                        "Material y requiere --subject-id")
    p.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    p.add_argument("--model", choices=MODELS,
                   help="Modelo WhisperX al crear un job con --source: large-v3 (por defecto), medium o "
                        "small; los ligeros son más rápidos sin GPU y menos precisos")
    p.add_argument("--start", type=float, default=0)
    p.add_argument("--duration", type=float, help="Fragmento de prueba, por ejemplo 300 o 600 segundos")
    p.add_argument("--oauth-client", help="JSON de cliente OAuth Desktop; usado solo con --login-drive")
    p.add_argument("--drive-credentials", help="Archivo OAuth externo al repositorio; nunca se copia al job")
    p.add_argument("--attach-analysis", metavar="MARKDOWN",
                   help="Adjuntar a --resume un análisis académico revisado de una clase aún local")
    p.add_argument("--analysis-transcript-sha256",
                   help="Huella de transcript.json obtenida con --status JOB_ID")
    p.add_argument("--analysis-reviewed", action="store_true",
                   help="Confirmación humana de revisión del informe y su fuente")
    p.add_argument("--publish", action="store_true", help="Publicar artefactos completos en --drive-root")
    p.add_argument("--drive-root", help="Raíz autorizada y montada de Academic Manager")
    p.add_argument("--register-database", action="store_true",
                   help="Registrar en 03 - Clases un job completo ya publicado; requiere --resume")
    p.add_argument("--subject-id", help="ID interno MAT-* usado al registrar la clase o al guardar material")
    p.add_argument("--database-state-file-id", help="Drive file ID de database-work-state.json")
    p.add_argument("--workbook-credentials",
                   help="Credencial ADC local para el Drive personal; por defecto control/service-account-drive.json")
    return p


def source_kind(source):
    """local, youtube o drive; YouTube se reconoce antes que la regla HTTPS de Drive."""
    if Path(source).is_file():
        return "local"
    if youtube.is_url(source):
        return "youtube"
    if source.startswith("https://") or re.fullmatch(r"[-A-Za-z0-9_]{10,}", source):
        return "drive"
    return "local"


def create_job(args):
    if not args.semester or not args.subject:
        raise CaptureError("Indique --semester y --subject.")
    if not math.isfinite(args.start) or args.start < 0:
        raise CaptureError("--start debe ser finito y no negativo.")
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        raise CaptureError("--duration debe ser positiva y finita.")
    if args.start and args.duration is None:
        raise CaptureError("Un recorte --start requiere --duration para identificarlo como prueba.")
    if args.transcript and (args.start or args.duration is not None):
        raise CaptureError("La importación no recorta transcripciones; los tiempos ya deben ser originales.")
    if args.class_date:
        from datetime import date
        try:
            date.fromisoformat(args.class_date)
        except ValueError as exc:
            raise CaptureError("--class-date debe ser YYYY-MM-DD.") from exc
    if args.tipo == "material":
        if args.class_date:
            raise CaptureError("El material de estudio no lleva fecha de clase; omita --class-date.")
        if not args.subject_id or not re.fullmatch(r"MAT-[A-Z0-9-]+", args.subject_id):
            raise CaptureError("El material de estudio requiere --subject-id MAT-... de la materia.")
    job = {"schema_version": 1, "job_id": uuid4().hex, "created_at": now(),
            "status": "created", "request": {
                "source": (str(Path(args.source).resolve()) if args.source and
                           source_kind(args.source) == "local" else args.source),
                "transcript": str(Path(args.transcript).resolve()) if args.transcript else None,
                "semester": args.semester, "subject": args.subject, "recording_name": args.recording_name,
                "class_date": args.class_date, "language": args.language, "device": args.device,
                "start": args.start, "duration": args.duration},
            "hashes": None, "publication_path": None}
    if args.source:
        job["request"]["model"] = args.model or DEFAULT_MODEL
    if args.tipo == "material":
        job["request"].update(tipo="material", subject_id=args.subject_id)
    return job


def run(args):
    args.runtime = paths.runtime(args.runtime, env=("ACADEMIC_MANAGER_RUNTIME", "ACADEMIC_MANAGER_DATA"),
                                 default=paths.windows_default())
    if not args.runtime:
        raise CaptureError("Indique --runtime con una carpeta absoluta local.")
    config = Config(Path(args.runtime))
    config.prepare()
    runtime = config.runtime.resolve()
    if args.watch is not None and (not args.status or not 5 <= args.watch <= 300):
        raise CaptureError("--watch requiere --status y un intervalo entre 5 y 300 segundos.")
    if args.model and not args.source:
        raise CaptureError("--model solo se usa al crear un job con --source; un job reanudado "
                           "conserva el modelo con el que se creó.")
    if args.status:
        if args.watch is None:
            print_status(runtime, args.status)
        else:
            while True:
                print_status(runtime, args.status)
                print(f"Actualización próxima en {args.watch} s; Ctrl+C detiene SOLO el monitor.", flush=True)
                time.sleep(args.watch)
        return None
    if args.register_database and not args.resume:
        raise CaptureError("--register-database solo opera sobre un job completo mediante --resume.")
    if args.attach_analysis and (not args.resume or args.publish or args.register_database):
        raise CaptureError("--attach-analysis exige --resume y una ejecución separada de publicación/registro.")
    if (args.analysis_reviewed or args.analysis_transcript_sha256) and not args.attach_analysis:
        raise CaptureError("--analysis-reviewed y --analysis-transcript-sha256 requieren --attach-analysis.")
    if args.login_drive:
        if args.publish:
            raise CaptureError("La autorización no publica artefactos.")
        auth.login(runtime, args.oauth_client)
        return
    job = None if args.resume else create_job(args)
    identity = args.resume or job["job_id"]
    work, temporary = job_paths(runtime, identity)
    for path in (work, temporary):
        if path.is_symlink():
            raise CaptureError("No se usan directorios de job redirigidos.")
        within(path, runtime)
    with job_lock(work):
        if args.resume:
            job = read_json(work / "job.json")
            if job.get("schema_version") != 1 or job.get("job_id") != identity:
                raise CaptureError("El job no corresponde al directorio o versión esperada.")
            recover(work, job)
            if args.attach_analysis:
                changed = attach(work, job, args.attach_analysis,
                                 args.analysis_transcript_sha256, args.analysis_reviewed)
                print("Análisis validado e incorporado." if changed else
                      "El análisis ya estaba incorporado; sin cambios.", flush=True)
                return job
        else:
            save(work, job)
        print(f"Job: {identity}", flush=True)
        request = job["request"]
        material = request.get("tipo") == "material"
        stage = work / "artifacts"
        try:
            if args.publish and (request["duration"] is not None or not args.drive_root):
                raise CaptureError("Un fragmento queda local; para publicar una clase completa indique --drive-root.")
            if args.register_database and material:
                raise CaptureError("El material de estudio no se registra como clase en 03 - Clases.")
            if args.drive_root:
                destination = Path(args.drive_root).resolve()
                if destination == runtime or destination.is_relative_to(runtime) or runtime.is_relative_to(destination):
                    raise CaptureError("Drive y runtime deben estar en raíces separadas.")
            if job["hashes"]:
                validate(stage, identity, job["hashes"])
            else:
                temporary.mkdir(parents=True, exist_ok=True)
                api = None
                if "source_metadata" not in job:
                    print("Leyendo metadata...", flush=True)
                    if request["transcript"]:
                        metadata = local.inspect(request["transcript"])
                        metadata["kind"] = "existing_transcript"
                        if request["recording_name"]:
                            metadata["original_name"] = request["recording_name"]
                    elif source_kind(request["source"]) == "local":
                        metadata = local.inspect(request["source"])
                    elif source_kind(request["source"]) == "youtube":
                        metadata = youtube.inspect(request["source"], request["language"])
                        if metadata["text_source"] == "manual_subtitles" and (
                                request["start"] or request["duration"] is not None):
                            raise CaptureError("Los subtítulos manuales no se recortan; omita --start/--duration.")
                    else:
                        drive.file_id(request["source"])
                        api = drive.service(args.drive_credentials, runtime)
                        metadata = drive.inspect(request["source"], api)
                    job["source_metadata"] = metadata
                    save(work, job, "metadata_ready")
                metadata = job["source_metadata"]
                normalized = work / "normalized.json"
                if job.get("normalized_sha256"):
                    if sha256(normalized) != job["normalized_sha256"]:
                        raise CaptureError("Cambió la transcripción intermedia; no se borra el medio.")
                    transcript = read_json(normalized)
                elif request["transcript"]:
                    if sha256(request["transcript"]) != metadata["sha256"]:
                        raise CaptureError("La transcripción importada cambió.")
                    transcript = normalize(import_transcript(request["transcript"]))
                    job["engine"] = {"engine": "import", "model": None}
                    atomic_json(normalized, transcript)
                    job["normalized_sha256"] = sha256(normalized)
                    save(work, job, "transcribed")
                elif metadata.get("text_source") == "manual_subtitles":
                    subtitles = work / "youtube-subtitles.json"
                    if not job.get("subtitles_sha256") or not subtitles.exists():
                        record_event(work, identity, "downloading")
                        print("Subtítulos manuales de YouTube encontrados; no se usa WhisperX...", flush=True)
                        job["subtitles_sha256"] = youtube.fetch_subtitles(
                            metadata, subtitles, job.get("subtitles_sha256"))
                        save(work, job, "downloaded")
                    elif sha256(subtitles) != job["subtitles_sha256"]:
                        raise CaptureError("Los subtítulos descargados cambiaron; revise el job.")
                    # Manual subtitles enter through the same importer as --transcript JSON.
                    transcript = normalize(import_transcript(subtitles))
                    job["engine"] = {"engine": "youtube_manual_subtitles", "model": None,
                                     "subtitle_language": metadata["subtitle_language"]}
                    atomic_json(normalized, transcript)
                    job["normalized_sha256"] = sha256(normalized)
                    save(work, job, "transcribed")
                else:
                    media, audio = temporary / "source.media", temporary / "audio.wav"
                    if not job.get("media_sha256") or not media.exists():
                        record_event(work, identity, "downloading")
                        print("Preparando copia temporal...", flush=True)
                        if metadata["kind"] == "local":
                            local.fetch(metadata, media)
                        elif metadata["kind"] == "youtube":
                            print("Sin subtítulos manuales; descargando solo el audio para WhisperX...", flush=True)
                            youtube.fetch(metadata, media, job.get("media_sha256"))
                        else:
                            api = api or drive.service(args.drive_credentials)
                            drive.fetch(metadata, media, api)
                        job["media_sha256"] = sha256(media)
                        save(work, job, "downloaded")
                    elif sha256(media) != job["media_sha256"]:
                        raise CaptureError("El medio temporal cambió; revise el job.")
                    job["media"] = probe(media, config.ffprobe)
                    if request["start"] >= job["media"]["duration_seconds"]:
                        raise CaptureError("El fragmento empieza después del final del medio.")
                    if not job.get("audio_sha256") or not audio.exists():
                        record_event(work, identity, "extracting_audio")
                        print("Extrayendo WAV mono 16 kHz...", flush=True)
                        job["processed_seconds"] = extract(media, audio, request["start"],
                                                          request["duration"], config.ffmpeg)
                        job["audio_sha256"] = sha256(audio)
                        save(work, job, "audio_ready")
                    elif sha256(audio) != job["audio_sha256"]:
                        raise CaptureError("El audio temporal cambió.")
                    record_event(work, identity, "transcribing")
                    reporter = ProgressReporter(work, identity)
                    raw, job["engine"] = transcribe(audio, request["language"], request["device"],
                                                        runtime / "procesamiento" / "model-cache",
                                                        progress_callback=reporter.update,
                                                        model=request.get("model", DEFAULT_MODEL))
                    reporter.update(100, force=True)
                    transcript = normalize(raw, request["start"], job["processed_seconds"])
                    atomic_json(normalized, transcript)
                    job["normalized_sha256"] = sha256(normalized)
                    save(work, job, "transcribed")
                stage.mkdir(parents=True, exist_ok=True)
                metadata_out = {"schema_version": 1, "job_id": identity,
                                "content_type": "Material" if material else "Clase", "source": metadata,
                                "semester": request["semester"], "subject": request["subject"],
                                "class_date": request["class_date"], "created_at": job["created_at"],
                                "fragment": {"is_test": request["duration"] is not None,
                                             "start_seconds": request["start"],
                                             "requested_seconds": request["duration"],
                                             "processed_seconds": job.get("processed_seconds")},
                                "media": job.get("media"), "transcription": job["engine"],
                                "analysis_status": "pending"}
                if material:
                    metadata_out["subject_id"] = request["subject_id"]
                atomic_json(stage / "metadata.json", metadata_out)
                atomic_json(stage / "transcript.json", transcript)
                (stage / "transcript.md").write_text(markdown(transcript, metadata["original_name"]),
                                                   encoding="utf-8", newline="\n")
                (stage / "analysis.md").write_text(pending(), encoding="utf-8", newline="\n")
                job["hashes"] = validate(stage, identity)
                save(work, job, "validated")
            if args.publish:
                if not args.drive_root:
                    raise CaptureError("Para publicar indique --drive-root.")
                record_event(work, identity, "publishing")
                target = publish(stage, args.drive_root, request["semester"], request["subject"],
                                 job["source_metadata"]["original_name"], job, lambda: save(work, job),
                                 "Material" if material else "Clases")
                save(work, job, "published")
                print(f"Publicación verificada por lectura y SHA-256: {target}", flush=True)
            if args.register_database:
                if not job.get("publication_path"):
                    raise CaptureError("La clase debe estar publicada antes de registrarse en la base.")
                if not args.subject_id or not args.database_state_file_id:
                    raise CaptureError("Indique --subject-id y --database-state-file-id.")
                record_event(work, identity, "registering_database")
                credential_path = Path(args.workbook_credentials).resolve() if args.workbook_credentials else (
                    runtime / "control" / "service-account-drive.json")
                user_api = None
                if not args.workbook_credentials and not credential_path.exists() and not credential_path.is_symlink():
                    # Sin cuenta de servicio: el inicio de sesión propio del asistente (alcance drive).
                    from .academic_manager.drive_workbook import user_service
                    user_api = user_service(runtime)
                if user_api is None and not credential_path.is_file():
                    raise CaptureError("No existe la credencial local del Drive personal.")
                from .academic_manager.database import register_class
                # Legacy registrations used a job-level completed_* status. The
                # reviewed-analysis workflow records approval in validated artifact metadata.
                registration_job = dict(job)
                if user_api is not None:
                    validate(stage, identity, job["hashes"])
                    if read_json(stage / "metadata.json").get("analysis_status") == "reviewed":
                        registration_job["analysis_status"] = "completed_reviewed"
                    registration = register_class(
                        runtime, registration_job, args.subject_id, args.database_state_file_id,
                        api=user_api)
                else:
                    previous_adc = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
                    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credential_path)
                    try:
                        validate(stage, identity, job["hashes"])
                        if read_json(stage / "metadata.json").get("analysis_status") == "reviewed":
                            registration_job["analysis_status"] = "completed_reviewed"
                        registration = register_class(
                            runtime, registration_job, args.subject_id, args.database_state_file_id)
                    finally:
                        if previous_adc is None:
                            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
                        else:
                            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = previous_adc
                job["database_registration"] = registration
                save(work, job, "database_registered")
                state = "ya registrada" if registration.get("already_registered") else "registrada"
                print(f"Clase {state}: {registration['class_id']}", flush=True)
            # Always re-read persisted artifacts immediately before deleting owned audiovisual copies.
            validate(stage, identity, job["hashes"])
            clean_media(temporary, runtime, identity)
            job["media_cleaned"] = True
            save(work, job, "completed")
            print(f"Completado. Job: {identity}\nArtefactos: {stage}", flush=True)
            return job
        except Exception as exc:
            job["last_error"] = {"type": type(exc).__name__, "message":
                                 str(exc) if isinstance(exc, CaptureError) else "Fallo inesperado; revisar entorno.",
                                 "at": now(), "failed_after": job["status"]}
            save(work, job, "failed")
            raise


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        run(args)
        return 0
    except KeyboardInterrupt:
        print("Interrumpido. Reanude el ID de job mostrado en procesamiento.", file=sys.stderr)
        return 130
    except (CaptureError, OSError, ValueError, KeyError) as exc:
        print(f"Capture: {exc}\nSe conserva job.json y el estado disponible. "
              "Reintente con --resume ID y la misma --runtime.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Capture: fallo inesperado ({type(exc).__name__}). Se conserva el job; "
              "revise las dependencias y reanude.", file=sys.stderr)
        return 1
