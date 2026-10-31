"""Interfaz estable de consulta académica; no escribe Drive ni Excel."""
import argparse
import json
from pathlib import Path
import sys
from academic_config import paths
from canvas_sync.common import SyncError
from .index import build, overview, search, load, activity_context


def main(argv=None):
    parser=argparse.ArgumentParser(description="Academic Manager Knowledge: índice y consulta local con fuentes.",
                                   epilog=paths.DATA_HELP)
    parser.add_argument("action",choices=("build","status","search","sources","activity"))
    parser.add_argument("--materia",help="ID de materia, p. ej. MAT-SER")
    parser.add_argument("--query",help="Palabras para búsqueda en documentos")
    parser.add_argument("--actividad",help="ID interno de actividad para ver relaciones verificables")
    parser.add_argument("--limit",type=int,default=8)
    args=parser.parse_args(argv)
    try:
        root=Path(paths.runtime(env=("ACADEMIC_MANAGER_DATA",),default=paths.DEFAULT_RUNTIME)).resolve()
        if args.action=="build":
            print(json.dumps(build(root),ensure_ascii=False,indent=2))
        elif args.action=="status":
            print(json.dumps(overview(root,args.materia),ensure_ascii=False,indent=2))
        elif args.action=="activity":
            if not args.actividad:
                raise SyncError("Indique --actividad ACT-...")
            print(json.dumps(activity_context(root,args.actividad),ensure_ascii=False,indent=2))
        elif args.action=="sources":
            data=load(root)[0]["data"]
            subset=[v for v in data["fuentes"].values()
                    if args.materia is None or v["materia_id"]==args.materia]
            print(json.dumps(subset,ensure_ascii=False,indent=2))
        else:
            if not args.query:
                raise SyncError("Indique --query para buscar información académica.")
            print(json.dumps(search(root,args.query,args.materia,args.limit),
                             ensure_ascii=False,indent=2))
        return 0
    except (SyncError,OSError,ValueError) as exc:
        print("Academic Knowledge:",str(exc),file=sys.stderr)
        return 1

if __name__=="__main__":
    sys.exit(main())
