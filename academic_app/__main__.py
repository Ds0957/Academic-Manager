"""python -m academic_app: abre en el navegador el panel del día a día, o el asistente de
configuración si la configuración todavía no está completa."""
import argparse
import sys
import webbrowser

from . import phone, server
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
    controller = _phone_access(wizard, srv)
    wizard.start_update_check()  # en segundo plano; sin internet no pasa nada
    minutes = int(server.IDLE_SECONDS // 60)
    if wizard.complete():
        wizard.land_on_panel()  # el enlace con la clave pone la cookie y luego lleva al panel
        print("Academic Manager: se abre el panel del día a día.", flush=True)
    else:
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
        if controller is not None:
            controller.stop()  # el celular solo entra mientras la app está abierta en el PC
        srv.server_close()
    return 0


def _phone_access(wizard, srv):
    """Acceso desde el celular (docs/42): solo con carpeta de datos; escucha si está activado."""
    runtime = wizard.data_folder()
    if runtime is None:
        return None

    def keep_alive():  # el uso desde el celular reinicia la cuenta de inactividad del PC
        srv.touch(+1)
        srv.touch(-1)

    controller = phone.PhoneController(wizard, phone.PhoneAccess(runtime), srv.action_lock, keep_alive)
    wizard.deps["phone"] = controller
    if controller.access.is_active():
        problem = controller.start()
        if problem:
            print("Acceso desde el celular:", problem, flush=True)
        else:
            print("Acceso desde el celular en", controller.status()["direccion"], flush=True)
    return controller


if __name__ == "__main__":
    sys.exit(main())
