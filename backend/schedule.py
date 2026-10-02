"""
Programacion de grabacion por equipo.

Guarda y valida el horario; la decision de grabar la toma el Agent con su
propio reloj (ver agent/scheduler.py), porque es el unico que sabe que
hora es en la maquina donde ocurre la grabacion y porque debe seguir
funcionando con el navegador cerrado y el panel sin nadie delante.

Dias: 0 = lunes ... 6 = domingo, como datetime.weekday().

Horario nocturno: si la hora final es MENOR que la inicial, la franja
cruza la medianoche (22:00-06:00). El dia de la semana se mira por el
momento de INICIO, asi que un turno de viernes noche sigue grabando la
madrugada del sabado aunque el sabado no este marcado.
"""

from backend.database import get_connection


DIAS_VALIDOS = (0, 1, 2, 3, 4, 5, 6)

NOMBRES_DIAS = (
    "lunes", "martes", "miercoles", "jueves",
    "viernes", "sabado", "domingo"
)

DEFECTO = {
    "enabled": False,
    "start_time": "08:00",
    "end_time": "17:00",
    "days": [0, 1, 2, 3, 4],
    "timezone": "local"
}


class ScheduleError(ValueError):
    """Horario rechazado, con un motivo que se puede ensenar."""


def parse_time(valor):
    """Convierte 'HH:MM' en minutos desde medianoche. Lanza si no vale."""

    if not isinstance(valor, str) or ":" not in valor:
        raise ScheduleError("La hora debe tener el formato HH:MM")

    partes = valor.split(":")

    if len(partes) != 2:
        raise ScheduleError("La hora debe tener el formato HH:MM")

    try:
        horas = int(partes[0])
        minutos = int(partes[1])

    except ValueError:
        raise ScheduleError("La hora debe tener el formato HH:MM")

    if not (0 <= horas <= 23) or not (0 <= minutos <= 59):
        raise ScheduleError("La hora debe estar entre 00:00 y 23:59")

    return horas * 60 + minutos


def format_time(minutos):

    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def parse_days(valor):
    """Acepta una lista de numeros o la cadena '0,1,2'. Devuelve lista."""

    if valor is None:
        raise ScheduleError("Hay que elegir al menos un dia")

    if isinstance(valor, str):
        trozos = [t.strip() for t in valor.split(",") if t.strip() != ""]
    else:
        trozos = list(valor)

    dias = set()

    for trozo in trozos:

        try:
            dia = int(trozo)
        except (TypeError, ValueError):
            raise ScheduleError("Los dias deben ser numeros del 0 al 6")

        if dia not in DIAS_VALIDOS:
            raise ScheduleError("Los dias deben ser numeros del 0 al 6")

        dias.add(dia)

    if not dias:
        raise ScheduleError("Hay que elegir al menos un dia")

    return sorted(dias)


def validate(datos):
    """
    Deja un horario en forma canonica, o lanza ScheduleError.

    No se admite una franja vacia (inicio igual a fin): seria una
    programacion que no graba nunca y parece que si.
    """

    activo = bool(datos.get("enabled"))

    inicio = parse_time(datos.get("start_time") or DEFECTO["start_time"])
    fin = parse_time(datos.get("end_time") or DEFECTO["end_time"])

    if inicio == fin:
        raise ScheduleError(
            "La hora de inicio y la de fin no pueden ser la misma"
        )

    dias = parse_days(
        datos.get("days") if datos.get("days") is not None
        else DEFECTO["days"]
    )

    zona = (datos.get("timezone") or "local").strip() or "local"

    if zona != "local":

        # Se comprueba que la zona exista aqui, y no en el Agent: mas vale
        # rechazarla al guardarla que descubrirlo cuando toque grabar.
        #
        # Windows no trae base de datos de zonas horarias, asi que sin el
        # paquete tzdata instalado NINGUN nombre se puede resolver. En ese
        # caso se dice con todas las letras, en vez de dejar al usuario
        # peleandose con un "zona desconocida" que no es culpa suya.
        try:
            from zoneinfo import ZoneInfo, available_timezones

            hay_base = bool(available_timezones())

        except Exception:
            hay_base = False

        if not hay_base:
            raise ScheduleError(
                "Este servidor no tiene base de datos de zonas horarias, "
                "asi que solo admite 'local' (el reloj del propio equipo). "
                "Para usar nombres como Europe/Madrid hay que instalar el "
                "paquete tzdata."
            )

        try:
            ZoneInfo(zona)

        except Exception:
            raise ScheduleError(f"Zona horaria desconocida: {zona}")

    return {
        "enabled": activo,
        "start_time": format_time(inicio),
        "end_time": format_time(fin),
        "days": dias,
        "timezone": zona
    }


def get_schedule(device_id):
    """Horario de un equipo. El predeterminado si no tiene ninguno."""

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT * FROM recording_schedule WHERE device_id = ?",
            (device_id,)
        ).fetchone()

    finally:
        connection.close()

    if fila is None:
        return dict(DEFECTO, device_id=device_id, updated_at=None)

    return {
        "device_id": device_id,
        "enabled": bool(fila["enabled"]),
        "start_time": fila["start_time"],
        "end_time": fila["end_time"],
        "days": parse_days(fila["days"]),
        "timezone": fila["timezone"],
        "updated_at": fila["updated_at"]
    }


def set_schedule(device_id, datos):
    """Guarda el horario de un equipo. Devuelve el horario ya normalizado."""

    from datetime import datetime, timezone as tz

    horario = validate(datos)

    connection = get_connection()

    try:
        connection.execute(
            """
            INSERT INTO recording_schedule (
                device_id, enabled, start_time, end_time,
                days, timezone, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                enabled = excluded.enabled,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                days = excluded.days,
                timezone = excluded.timezone,
                updated_at = excluded.updated_at
            """,
            (
                device_id,
                1 if horario["enabled"] else 0,
                horario["start_time"],
                horario["end_time"],
                ",".join(str(d) for d in horario["days"]),
                horario["timezone"],
                datetime.now(tz.utc).isoformat()
            )
        )

        connection.commit()

    finally:
        connection.close()

    return get_schedule(device_id)


def describe(horario):
    """Resumen legible, para la interfaz y la auditoria."""

    if not horario.get("enabled"):
        return "Programacion desactivada"

    dias = horario.get("days") or []

    if dias == [0, 1, 2, 3, 4]:
        etiqueta = "lunes a viernes"
    elif dias == list(DIAS_VALIDOS):
        etiqueta = "todos los dias"
    else:
        etiqueta = ", ".join(NOMBRES_DIAS[d] for d in dias)

    return (
        f"{etiqueta} de {horario['start_time']} a {horario['end_time']}"
    )
