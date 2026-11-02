# 42 — Acceso desde el celular

**Estado:** implementado y probado localmente (WSL, con la red y el código QR simulados); pendiente de probar en un PC Windows real con un celular. Rama `feat/panel-celular`.

## Para qué sirve

Abre el panel del día a día ([41-panel.md](41-panel.md)) desde el celular, conectado al **mismo Wi-Fi** que el PC: ver **Hoy, Resumen, Materias e Instrucciones** y **registrar** entregas, notas, tareas y decisiones con la misma vista previa y «Aprobar y publicar». Viene **apagado**: el PC solo escucha la red mientras usted lo tenga activado y Academic Manager esté abierto.

El celular no puede: abrir Configuración, cerrar Academic Manager, actualizarlo, pulsar «Sincronizar ahora» ni hacer los pasos de Canvas o Google. Esas páginas responden «No disponible desde el celular». En el celular el menú muestra solo Hoy · Resumen · Materias · Instrucciones y **Salir**.

## Cómo activarlo

1. En el PC, abra Academic Manager → **Configuración** → **Acceso desde el celular** (debajo de los pasos).
2. Elija un **PIN** de 6 a 12 números, repítalo y pulse **Activar**. El PIN se guarda cifrado (PBKDF2), nunca tal cual.
3. La página muestra la dirección para el celular (`http://<IP del PC>:47321`). Pulse **Mostrar código QR**.
4. Escanee el QR con la cámara del celular. El código es **válido 5 minutos, un solo uso**; si vence, genere otro.
5. El celular queda autorizado y pide el PIN. Escríbalo y entra al panel.

La primera vez, Windows puede preguntar si permite a **Python** usar la red: permítalo solo en **redes privadas**.

Si este PC no tiene un programa para dibujar el QR, la página muestra la dirección completa como texto: escríbala en el navegador del celular (sirve igual, 5 minutos y un solo uso).

## Cómo funciona la entrada

- **Autorizar el celular (QR):** una clave larga de un solo uso. El celular recibe una cookie que dura 180 días; el PC solo guarda su huella SHA-256 en `control\celular\acceso.json` de la carpeta de datos.
- **Entrar (PIN):** cada vez que abre el panel en el celular. La sesión dura hasta 12 horas y termina al cerrar Academic Manager en el PC. Cinco PIN equivocados bloquean la entrada 15 minutos para todos los celulares. Un celular sin autorizar nunca ve el formulario del PIN.
- Usar el panel desde el celular cuenta como uso: Academic Manager no se cierra por inactividad mientras lo usa. Al cerrar Academic Manager en el PC, el celular deja de entrar.

## Gestionar celulares

En **Configuración → Acceso desde el celular**:

- **Celulares autorizados:** nombre (iPhone, Android…), «autorizado el…» y «visto por última vez el…». **Quitar** lo desautoriza al instante.
- **Cambiar PIN:** todos los celulares tendrán que escribir el PIN nuevo.
- **Desactivar:** el PC deja de escuchar la red. Los celulares autorizados se conservan para cuando lo vuelva a activar.

## Seguridad y límites

- Dentro del Wi-Fi la conexión **no está cifrada** (es `http`, no `https`): el PIN y las cookies viajan sin cifrar por esa red. Úselo solo en su casa o en redes de su confianza; **nunca en un Wi-Fi público ni en el de la universidad**.
- El PC solo escucha en su dirección de la red privada (nunca si el PC está en una red pública con dirección pública) y solo acepta celulares con dirección privada. Revisa la dirección y el origen de cada petición y pide un token por formulario.
- El registro de la ventana nunca guarda códigos QR, PIN ni cookies.
- El panel del PC (`127.0.0.1`) no cambia.

## Si algo no funciona

- **El celular no abre la dirección:** revise que los dos estén en el mismo Wi-Fi y que Academic Manager siga abierto en el PC. Si al activar Windows preguntó por el firewall y eligió «Cancelar» o solo redes públicas, permita Python en redes privadas en «Firewall de Windows → Permitir una aplicación».
- **Cambió la dirección del PC** (otro Wi-Fi o el router le dio otra IP): abra la página en el PC para ver la dirección nueva y vuelva a escanear el QR.
- **«El puerto 47321 está ocupado por otro programa»:** cierre ese programa o reinicie el PC y pulse **Volver a intentar**.
- **«El PC no está conectado a una red privada»:** conéctelo al Wi-Fi de casa y pulse **Volver a intentar**.
- **«El código QR venció» o «ya se usó»:** genere otro en el PC.
- **«Demasiados intentos con un PIN equivocado»:** espere 15 minutos. Si olvidó el PIN, cámbielo en el PC.
