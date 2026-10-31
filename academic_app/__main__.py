"""python -m academic_app: abre el asistente de configuración en el navegador."""
import argparse
import sys
import webbrowser

from . import server
from .wizard import Wizard


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m academic_app",
                                description="Asistente de configuración de Academic Manager. Se abre en "
                                            "el navegador y solo acepta conexiones de este PC.")
    p.add_argument("--sin-navegador", action="store_true",
                   help="No abrir el navegador; solo mostrar la dirección")
    args = p.parse_args(argv)
    wizard = Wizard()
    srv = server.create(wizard)
    wizard.start_update_check()  # en segundo plano; sin internet no pasa nada
    minutes = int(server.IDLE_SECONDS // 60)
    print("Asistente de configuración de Academic Manager.", flush=True)
    print("Si el navegador no se abre solo, copie esta dirección en él:", srv.url, flush=True)
    print(f"Para terminar pulse «Cerrar el asistente» en la página; se cierra solo tras {minutes} "
          "minutos sin uso.", flush=True)
    if not args.sin_navegador:
        webbrowser.open(srv.url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
