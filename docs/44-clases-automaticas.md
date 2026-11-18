# Clases automáticas

Academic Manager busca solo las grabaciones nuevas de cada clase y las transcribe sin preguntar. El índice por minutos lo sigue haciendo Claude, y se guarda solo con su aprobación ([43](43-conector-claude.md), `completar_clase`).

## De dónde salen las grabaciones

En el último paso de la rutina diaria (y de «Sincronizar ahora», [41](41-panel.md)), `python -m academic_operations.auto_classes` lee el contenido de Canvas ya sincronizado de cada materia activa: tareas, foros, anuncios y páginas ([17](17-canvas-sync-enriquecido.md)). Cada enlace de Google Drive toma la última fecha escrita antes que él (`canvas_sync/recordings.py`):

- **Foro con la fecha y el enlace debajo:** «1 de octubre» y el enlace, «8 octubre» y el enlace.
- **Página con bloques por sesión:** «Date: 5th of August … Link to access: Session 0», donde el enlace está detrás del texto «Session 0». Para esto, la sincronización de Canvas guarda el texto de cada enlace de Drive (`anchors`).

Lee fechas en español («1 de octubre»), en inglés («5th of August», «November 4»), ISO y numéricas («21 - 10 - 2026»). El año es el de la sincronización, salvo que la fecha caiga más de 7 días después: entonces es del año anterior. Una línea «Date:» o «Fecha:» que no se entiende no hereda la fecha anterior.

## Qué se transcribe

Con el inicio de sesión de Drive de Capture, se pregunta una sola vez por cada archivo qué es y si se puede descargar (hasta 40 por corrida; lo que falla se reintenta en la siguiente). Estas consultas se hacen sin bloquear la lista, para que una transcripción que termina en ese momento pueda seguir con la siguiente. Lo mismo al lanzar la siguiente grabación: la vista previa con Drive corre sin la lista bloqueada, y lo que cambió en cada grabación (intentos, problema, rechazo) se guarda al final sobre la lista más reciente, también si algo falla a mitad de camino. Lo que no es audio ni video se ignora. Lo que no se puede descargar se vuelve a mirar al día siguiente, por si el profesor habilita la descarga. La lista queda en `procesamiento/clases-automaticas/encontradas.json`.

Después se lanza Capture, en el mismo proceso aparte que usa Claude ([43](43-conector-claude.md)), sobre la grabación descargable más antigua que falte. Al terminar, ese proceso sigue con la siguiente; se procesa una a la vez. Se salta lo que ya tiene una clase registrada para esa materia y fecha, lo que ya está transcrito y lo que falló dos veces. Un pedido rechazado queda anotado y se intenta la siguiente. Si el rechazo no va a cambiar solo (el nombre de la grabación dice otra fecha, el archivo no es audio ni video, o ya hay un trabajo sin terminar con otros datos), esa grabación no se vuelve a intentar sola: queda en «Mi semana» con su motivo, y se intenta de nuevo si su fecha o su materia cambian en Canvas o en Calendar. Los demás rechazos (por ejemplo, Drive no respondió) se reintentan en la siguiente corrida. Si una grabación aparece en Canvas y en Calendar, valen la materia y la fecha de Canvas. Este paso nunca cambia el resultado de la sincronización de Canvas.

## Google Calendar

Con el permiso opcional de solo lectura de Calendar ([38](38-asistente-configuracion.md), paso de Google), el mismo paso diario lee primero el calendario principal: las tres semanas anteriores y la siguiente (`academic_operations/calendar_classes.py`). Cada evento se asigna a la materia cuyo nombre aparece completo en su título («Sesión Sistemas Operativos II», «4146_GESTION DE BASES DE DATOS»); los eventos de todo el día y los que no son de ninguna materia se ignoran. Se guardan su fecha y hora de Bogotá y las grabaciones (video o audio) adjuntas, en `procesamiento/clases-automaticas/calendario.json`. Esas grabaciones entran a la misma lista que las de Canvas, con fuente «Google Calendar».

Si Calendar falla, se conserva el horario anterior. Si falta el permiso, el paso de Google del asistente lo explica (sin conectarse: lee `calendario-estado.json`, el resultado de la última lectura).

## Qué se ve en el panel

En «Mi semana» y en «Clases», cada grabación encontrada aparece con su estado:

- **En cola:** espera su turno para transcribirse.
- **Sin grabación todavía:** una clase del horario de Calendar de un día anterior que todavía no tiene grabación ni registro (la de hoy aparece desde mañana) («Mi semana» muestra las de los últimos 7 días).
- **Solo transcripción:** Drive no deja descargarla. Tiene el enlace «Abrir grabación»: se abre, se pulsa el botón «Transcripción de Drive» y se le pide a Claude que importe las transcripciones descargadas ([43](43-conector-claude.md), `importar_transcripciones`).

Cuando Capture empieza, la grabación pasa a ser una clase «Procesando», y luego «Transcrita, falta el índice».

## Límites

- Un foro escrito por un estudiante con una fecha y un video de Drive también se transcribiría: la instantánea de Canvas no guarda quién escribió cada foro. Solo pasan videos o audios descargables, y el índice siempre necesita aprobación.
- Una grabación adjunta a cualquier evento del calendario principal cuyo título contenga el nombre de una materia entra a la cola, sin mirar quién organizó el evento (las invitaciones de otras personas también llegan a ese calendario). Solo pasan videos o audios descargables, y el índice siempre necesita aprobación.
- Una materia se reconoce en Calendar solo si todas las palabras de su nombre (sin tildes ni palabras cortas) están en el título del evento; «Inglés» también cuenta como «English».
