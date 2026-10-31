"""Índice local de conocimiento a partir de Excel, Drive y Canvas Study.

El SQLite y el manifiesto son reconstruibles; no reemplazan a las fuentes.
"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
import tempfile

from academic_config import paths
from academic_store import store
from capture.storage.paths import safe_name
from canvas_sync.common import SyncError, atomic_json
from canvas_sync.semester import load_registry
from canvas_sync.routine import workbook_version
from canvas_sync.sync import sha

DEFAULT_DRIVE_ROOT = Path(r"G:\Mi unidad\03 - Universidad\Academic Manager")


def configured_drive_root():
    """drive_root del archivo de configuración del usuario o, si no está, la ruta de siempre."""
    return paths.drive_root(DEFAULT_DRIVE_ROOT)


# Se resuelve al importar para que build() y la ruta autorizada de academic_access.core
# vean la misma raíz. Un archivo inválido no rompe la importación: los comandos lo
# vuelven a leer al resolver su runtime y se detienen con su propio error.
try:
    DRIVE_ROOT = configured_drive_root()
except paths.ConfigError:
    DRIVE_ROOT = DEFAULT_DRIVE_ROOT
MAX_TEXT = 3_000_000
MAX_SOURCES = 5000


def hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blob in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blob)
    return h.hexdigest()


def safe_path(root, *parts):
    root = Path(root).resolve()
    result = root
    for part in parts:
        if (not isinstance(part, str) or part in ("", ".", "..")
                or Path(part).name != part or "/" in part or "\\" in part or ":" in part):
            raise SyncError("Ruta de fuente no autorizada.")
        result = result / part
        if result.is_symlink():
            raise SyncError("Archivo de origen redirigido.")
    if not result.resolve().is_relative_to(root):
        raise SyncError("La fuente sale del directorio autorizado.")
    return result


def excel_catalog(runtime):
    """Catálogo canónico desde la base SQLite publicada (mismas hojas y forma que V2)."""
    workbook_version(runtime)
    try:
        return store.read_catalog(Path(runtime) / store.DB_NAME,
                                  ("01 - Materias", "02 - Actividades", "03 - Clases",
                                   "04 - Recursos", "05 - Semestres"))
    except store.StoreError as exc:
        raise SyncError(str(exc)) from exc


def eje_of(text):
    match = re.search(r"\beje\s*[-:]?\s*([1-8])\b", str(text or ""), re.I)
    return int(match.group(1)) if match else None


def add(documents, key, subject, kind, title, *, eje=None, path=None,
        pdf=None, pdf_sha=None, url=None, status="metadata", external_id=None):
    if key in documents or len(documents) >= MAX_SOURCES:
        raise SyncError("Identificador duplicado o límite de fuentes excedido.")
    entry = {"fuente_id": key, "materia_id": subject, "tipo": kind,
             "titulo": str(title or key), "eje": eje, "ruta_texto": str(path) if path else None,
             "ruta_pdf": str(pdf) if pdf else None, "origen": url, "estado": status,
             "sha256_texto": None, "sha256_pdf": pdf_sha, "id_externo": external_id}
    if pdf_sha and pdf:
        if not pdf.exists():
            entry["estado"] = "pdf_no_disponible"
        elif hash_file(pdf) != pdf_sha:
            raise SyncError("Hash de PDF no coincide con índice de Canvas.")
    if path and not path.is_file():
        raise SyncError(f"La fuente publicada no está accesible: {key}.")
    if path and path.is_file():
        if path.is_symlink() or path.stat().st_size > MAX_TEXT:
            raise SyncError("Fuente de texto no autorizada o demasiado grande.")
        payload = path.read_bytes()
        try:
            payload.decode("utf-8-sig")
        except UnicodeError as exc:
            raise SyncError("Texto origen no es UTF-8.") from exc
        entry.update(estado="texto", sha256_texto=hashlib.sha256(payload).hexdigest())
    elif path:
        entry["estado"] = "texto_no_disponible"
    documents[key] = entry


def study_sources(documents, runtime, registry):
    base = Path(runtime) / "procesamiento" / "canvas-sync" / "study"
    for course in registry["courses"]:
        cid, mid = course["course_id"], course["subject_id"]
        folder = safe_path(base, str(cid))
        index = folder / "index.json"
        if not index.is_file():
            continue
        raw = json.loads(index.read_text(encoding="utf-8"))
        data = raw.get("data", {})
        if (raw.get("sha256") != sha(data) or data.get("course_id") != cid
                or data.get("source") != "canvas_html_and_institutional_get_only"):
            raise SyncError("Índice académico Canvas alterado o de curso diferente.")
        for eje in data.get("ejes", []):
            number = eje["eje"]
            if type(number) is not int or not 1 <= number <= 8:
                raise SyncError("Número de eje inválido.")
            directory = safe_path(folder, f"eje{number}")
            def register(role, metadata, source_id, title, where):
                pdf = safe_path(where, metadata["pdf_file"])
                txt = safe_path(where, metadata["text_file"]) if metadata.get("text_file") else None
                add(documents, source_id, mid, role, title, eje=number, path=txt,
                    pdf=pdf, pdf_sha=metadata["sha256"], url=metadata.get("source_url"),
                    status=metadata.get("extraction_status", "metadata"),
                    external_id=eje.get("canvas_html_file_id"))
            register("referente", eje["referente"], f"referente:{cid}:{number}",
                     eje.get("title"), directory)
            for reading in eje.get("readings", []):
                i = reading["index"]
                if type(i) is not int or not 1 <= i <= 24:
                    raise SyncError("Lectura de eje inválida.")
                key = f"lectura:{cid}:{number}:{i}"
                if reading.get("document"):
                    register("lectura", reading["document"], key, reading.get("title"),
                             safe_path(directory, "lecturas", str(i)))
                else:
                    add(documents, key, mid, "lectura_externa", reading.get("title"),
                        eje=number, url=reading.get("url"), status="no_descargado")
            for i, media in enumerate(eje.get("media",[]),1):
                if not isinstance(media,dict):
                    continue
                add(documents, f"multimedia:{cid}:{number}:{i}", mid,"multimedia",
                    media.get("title") or media.get("label") or "Multimedia",
                    eje=number, url=media.get("url"),status="no_descargado")


def _drive_source(relative, root):
    if not isinstance(relative,str) or not relative or "\\" in relative:
        raise SyncError("Ruta de artefacto de clase inválida.")
    return safe_path(root, *relative.split("/"))


def material_sources(documents, subjects, drive_root):
    """Material de estudio publicado por Capture en <semestre>/<materia>/Material."""
    for mid, meta in subjects.items():
        try:
            # Mismos nombres de carpeta que usa el publicador de Capture.
            folder = safe_path(drive_root, safe_name(meta["semestre"]), safe_name(meta["nombre"]),
                               "Material")
        except SyncError:
            continue
        if not folder.is_dir():
            continue
        for item in sorted(folder.iterdir()):
            # Publicación incompleta (marcador de Capture presente) o archivo ajeno: no es fuente.
            if (not item.is_dir() or item.is_symlink() or (item / ".capture-owner.json").exists()
                    or not (item / "metadata.json").is_file()):
                continue
            try:
                metadata = json.loads(safe_path(item, "metadata.json").read_text(encoding="utf-8"))
            except ValueError:  # truncado, p. ej. por una sincronización de Drive interrumpida
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            job_id = metadata.get("job_id")
            if (metadata.get("content_type") != "Material" or metadata.get("subject_id") != mid
                    or not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id)):
                raise SyncError("Material publicado de otra materia o con metadata inválida: "
                                f"{item.relative_to(drive_root).as_posix()}")
            source = metadata.get("source") or {}
            add(documents, f"material:{job_id}", mid, "material",
                source.get("original_name") or item.name,
                path=safe_path(item, "transcript.md"), url=source.get("location"))


def chunk_text(text, limit=1500):
    paragraphs = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    carry = ""
    for text in paragraphs:
        piece = text.strip()
        if not piece:
            continue
        while len(piece) > limit:
            if carry:
                yield carry
                carry = ""
            yield piece[:limit]
            piece = piece[limit:]
        if carry and len(carry) + len(piece) > limit:
            yield carry
            carry = ""
        carry += ("\n\n" if carry else "") + piece
    if carry:
        yield carry


def build(runtime, drive_root=DRIVE_ROOT):
    runtime = Path(runtime).resolve()
    registry = load_registry(runtime)
    excel = excel_catalog(runtime)
    subjects = {str(x["materia_id"]): {"nombre": str(x["nombre"]),
                "semestre": str(x["semestre"]), "canvas_id": None}
                for x in excel["01 - Materias"] if x.get("materia_id")}
    for course in registry["courses"]:
        if course["subject_id"] not in subjects:
            raise SyncError("La matrícula Canvas no coincide con Excel.")
        subjects[course["subject_id"]]["canvas_id"] = course["course_id"]
    documents = {}
    for row in excel["03 - Clases"]:
        mid, class_id = str(row["materia_id"]), str(row["clase_id"])
        if mid not in subjects:
            raise SyncError("Clase asociada a materia desconocida.")
        for key, field in (("analisis","analisis"),("transcripcion","transcripcion")):
            relative = row.get(field)
            if not relative:
                continue
            parts = relative.split("/")
            if (parts[0] != subjects[mid]["semestre"]
                    or len(parts) < 5 or parts[1] != subjects[mid]["nombre"]
                    or parts[2] != "Clases"):
                raise SyncError("Ruta de clase fuera de semestre/Clases.")
            add(documents,f"clase:{class_id}:{key}",mid,key,row.get("titulo"),
                path=_drive_source(relative,drive_root),
                url=f"Excel 03 - Clases:{class_id}")
    study_sources(documents,runtime,registry)
    material_sources(documents,subjects,drive_root)
    course_for = {x["subject_id"]:x["course_id"] for x in registry["courses"]}
    for row in excel["04 - Recursos"]:
        mid = str(row.get("materia_id") or "")
        if mid not in subjects:
            raise SyncError("Recurso de materia desconocida.")
        rid = str(row["recurso_id"])
        external = str(row.get("objeto_externo_id") or "").removesuffix(".0")
        kw = {"eje":eje_of(row.get("titulo")) or eje_of(row.get("descripcion")),
              "url":row.get("url"),"external_id":external or None}
        # Vinculación fuerte por ID Canvas verificado en Excel y metadata local.
        if external.isdecimal() and mid in course_for:
            folder=safe_path(runtime/"procesamiento"/"canvas-sync"/"pdfs",
                             str(course_for[mid]),external)
            latest=folder/"latest.json"
            if latest.is_file():
                meta=json.loads(latest.read_text(encoding="utf-8"))
                if (meta.get("course_id")!=course_for[mid]
                    or str(meta.get("file_id"))!=external
                    or meta.get("source")!="canvas_api_file_metadata_and_signed_read"):
                    raise SyncError("Guía Canvas con ID/procedencia inconsistente.")
                if meta.get("relative_text") and meta.get("relative_document"):
                    add(documents,f"recurso:{rid}",mid,str(row.get("tipo") or "guia"),
                        row.get("titulo"),
                        path=safe_path(folder,meta["relative_text"]),
                        pdf=safe_path(folder,meta["relative_document"]),
                        pdf_sha=meta["sha256"],**kw)
                    continue
        add(documents,f"recurso:{rid}",mid,str(row.get("tipo") or "recurso"),
            row.get("titulo"),status="enlace_verificado_sin_texto_local",**kw)
    activities = []
    relations = []
    # Relación explícita únicamente cuando la anotación académica ya menciona
    # el ID interno exacto de la actividad. No inferir por semejanza de títulos.
    known_activities = {str(x.get("actividad_id")):str(x.get("materia_id"))
                        for x in excel["02 - Actividades"] if x.get("actividad_id")}
    for resource in excel["04 - Recursos"]:
        note = str(resource.get("notas") or "")
        sid = str(resource.get("materia_id") or "")
        for aid in set(re.findall(r"ACT-[A-Z0-9-]+",note)):
            if known_activities.get(aid) == sid:
                relations.append({"actividad_id":aid,"fuente_id":f"recurso:{resource['recurso_id']}",
                                  "tipo":"guia_asociada_en_excel",
                                  "certeza":"id_explicito_en_notas_del_recurso"})
    for row in excel["02 - Actividades"]:
        mid = str(row.get("materia_id") or "")
        if mid not in subjects:
            raise SyncError("Actividad de materia desconocida.")
        aid = str(row.get("actividad_id"))
        eje = eje_of(row.get("organizacion")) or eje_of(row.get("titulo"))
        activities.append({"actividad_id":aid,"materia_id":mid,"titulo":str(row.get("titulo") or ""),
                           "tipo":str(row.get("tipo") or ""),"eje":eje,
                           "estado_entrega":row.get("estado_entrega"),
                           "estado_calificacion":row.get("estado_calificacion")})
        if eje:
            relations.extend({"actividad_id":aid,"fuente_id":source["fuente_id"],
                              "tipo":"contexto_del_mismo_eje",
                              "certeza":"contextual_no_es_consigna"}
                             for source in documents.values()
                             if source["materia_id"] == mid and source["eje"] == eje
                             and source["tipo"] in ("referente","lectura","lectura_externa","guia")
                             and not any(link["actividad_id"]==aid and
                                         link["fuente_id"]==source["fuente_id"]
                                         for link in relations))
    base = {"schema_version":1,"origen":"fuentes_verificadas_derivadas",
            "workbook_sha256":workbook_version(runtime),"materias":subjects,
            "fuentes":documents,"actividades":activities,"relaciones":relations}
    signature = sha(base)
    destination = runtime / "procesamiento" / "academic-knowledge"
    destination.mkdir(parents=True,exist_ok=True)
    manifest, database = destination/"index.json",destination/"search.sqlite3"
    if manifest.is_symlink() or database.is_symlink():
        raise SyncError("Índice local redirigido.")
    if manifest.is_file() and database.is_file():
        prior=json.loads(manifest.read_text(encoding="utf-8"))
        if prior.get("sha256") == signature:
            return {"changed":False,"sha256":signature,"materias":len(subjects),
                    "fuentes":len(documents),"con_texto":sum(x["estado"]=="texto" for x in documents.values()),
                    "relaciones":len(relations),"path":str(destination)}
    fd,tempname=tempfile.mkstemp(prefix="knowledge-",suffix=".sqlite3",dir=destination)
    os.close(fd)
    temp=Path(tempname)
    try:
        db=sqlite3.connect(temp)
        try:
            db.execute("CREATE TABLE passages(source_id TEXT,materia_id TEXT,kind TEXT,eje INTEGER,body TEXT)")
            db.execute("CREATE VIRTUAL TABLE passages_fts USING fts5(body,content='passages',content_rowid='rowid',tokenize='unicode61 remove_diacritics 2')")
            passages=0
            for source in documents.values():
                if source["estado"] != "texto":
                    continue
                path=Path(source["ruta_texto"])
                contents=path.read_bytes()
                if hashlib.sha256(contents).hexdigest()!=source["sha256_texto"]:
                    raise SyncError("La fuente cambió durante la indexación.")
                for paragraph in chunk_text(contents.decode("utf-8-sig")):
                    id=db.execute("INSERT INTO passages(source_id,materia_id,kind,eje,body) VALUES(?,?,?,?,?)",
                        (source["fuente_id"],source["materia_id"],source["tipo"],source["eje"],paragraph)).lastrowid
                    db.execute("INSERT INTO passages_fts(rowid,body) VALUES(?,?)",(id,paragraph))
                    passages+=1
            db.commit()
        finally:
            db.close()
        os.replace(temp,database)
    finally:
        temp.unlink(missing_ok=True)
    atomic_json(manifest,{"sha256":signature,"data":base,"pasajes":passages,
                         "generado_utc":datetime.now(timezone.utc).isoformat()})
    return {"changed":True,"sha256":signature,"materias":len(subjects),
            "fuentes":len(documents),"con_texto":sum(x["estado"]=="texto" for x in documents.values()),
            "relaciones":len(relations),"pasajes":passages,"path":str(destination)}


def load(runtime):
    folder=Path(runtime)/"procesamiento"/"academic-knowledge"
    index=json.loads((folder/"index.json").read_text(encoding="utf-8"))
    if index.get("sha256")!=sha(index["data"]):
        raise SyncError("El índice académico no supera verificación de integridad.")
    return index,folder/"search.sqlite3"


def overview(runtime,subject=None):
    index,_=load(runtime)
    data=index["data"]
    if subject and subject not in data["materias"]:
        raise SyncError("Materia no registrada.")
    result={}
    for mid,meta in data["materias"].items():
        if subject and subject!=mid:
            continue
        sources=[x for x in data["fuentes"].values() if x["materia_id"]==mid]
        result[mid]={"materia":meta["nombre"],"semestre":meta["semestre"],
            "actividades":sum(x["materia_id"]==mid for x in data["actividades"]),
            "fuentes":len(sources),"con_texto":sum(x["estado"]=="texto" for x in sources),
            "ejes":sorted({x["eje"] for x in sources if x["eje"]}),
            "relaciones_contextuales":sum(x["fuente_id"] in {s["fuente_id"] for s in sources} for x in data["relaciones"])}
    return result


def activity_context(runtime,activity_id):
    index,_ = load(runtime)
    data=index["data"]
    chosen=next((a for a in data["actividades"] if a["actividad_id"]==activity_id),None)
    if chosen is None:
        raise SyncError("Actividad no registrada en el índice.")
    related=[]
    for link in data["relaciones"]:
        if link["actividad_id"] == activity_id:
            src=data["fuentes"][link["fuente_id"]]
            related.append({**link,"titulo":src["titulo"],"tipo_fuente":src["tipo"],
                            "estado_fuente":src["estado"],"ruta_texto":src["ruta_texto"],
                            "origen":src["origen"]})
    classes=[{"fuente_id":s["fuente_id"],"titulo":s["titulo"],"ruta_texto":s["ruta_texto"]}
             for s in data["fuentes"].values()
             if s["materia_id"]==chosen["materia_id"] and s["tipo"]=="analisis"]
    return {"actividad":chosen,"fuentes_del_eje":related,
            "analisis_de_clases_de_la_materia":classes,
            "aviso":"La coincidencia de eje es contextual; solo guia_asociada_en_excel representa una vinculación explícita. Una clase de la materia no demuestra que haya tratado esa actividad."}


def search(runtime,query,subject=None,limit=8):
    index,dbfile=load(runtime)
    if subject and subject not in index["data"]["materias"]:
        raise SyncError("Materia no registrada.")
    if not isinstance(query,str) or not query.strip() or len(query)>250:
        raise SyncError("Búsqueda vacía o demasiado larga.")
    common={"de","del","en","la","las","el","los","que","un","una","y",
            "a","al","por","para","se","con","es","su","sus","sobre","como"}
    words=re.findall(r"\w+",query,re.UNICODE)[:12]
    relevant=[w for w in words if w.casefold() not in common]
    if not relevant:
        relevant=words
    if not relevant:
        raise SyncError("Búsqueda sin palabras.")
    db=sqlite3.connect(f"file:{dbfile.as_posix()}?mode=ro",uri=True)
    statement=(
        "SELECT p.source_id,p.materia_id,p.kind,p.eje,"
        "snippet(passages_fts,0,'[',']','…',20),bm25(passages_fts) "
        "FROM passages_fts JOIN passages p ON p.rowid=passages_fts.rowid "
        "WHERE passages_fts MATCH ? AND (? IS NULL OR p.materia_id=?) "
        "ORDER BY bm25(passages_fts) LIMIT ?")
    try:
        quoted=['"'+word+'"' for word in relevant]
        rows=db.execute(statement,(" AND ".join(quoted),subject,subject,
                                   min(max(1,limit),30))).fetchall()
        if not rows and len(quoted)>1:
            rows=db.execute(statement,(" OR ".join(quoted),subject,subject,
                                       min(max(1,limit),30))).fetchall()
    finally:
        db.close()
    return [{"fuente_id":sid,"materia_id":mid,"tipo":typ,"eje":eje,
             "titulo":index["data"]["fuentes"][sid]["titulo"],
             "fragmento":text,"ruta_fuente":index["data"]["fuentes"][sid]["ruta_texto"],
             "origen":index["data"]["fuentes"][sid]["origen"],
             "relevancia":rank} for sid,mid,typ,eje,text,rank in rows]
