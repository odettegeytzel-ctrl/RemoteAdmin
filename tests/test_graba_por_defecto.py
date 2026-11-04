r"""
Un equipo nuevo nace grabando.

    .venv\Scripts\python tests/test_graba_por_defecto.py

Antes nacia SIN horario y con el interruptor de la grabacion continua
apagado, y habia que configurarlo a mano uno por uno. Con siete
equipos es una molestia. Con treinta es una tarde perdida y la
certeza de que alguno se queda sin grabar sin que nadie se entere
hasta el dia que haga falta esa grabacion.

Lo que se pidio, literal: "se puede activar cada uno por default y
desactivar desde el panel".

Esas son las dos mitades, y la segunda importa tanto como la
primera:

  nace encendido          el alta aplica el horario de la empresa

  se apaga y SE QUEDA     apagarlo en el panel es una decision de
  apagado                 una persona, y nada puede deshacerla sola

La segunda mitad es la que convierte un valor por defecto en algo
usable. Si el ajuste de empresa se reaplicara cada cierto tiempo, o
en cada reinicio, apagar un equipo duraria hasta el siguiente ciclo
y nadie relacionaria una cosa con la otra. Ese error ya se cometio
una vez en este proyecto, con la migracion del interruptor, y por
eso aquella lleva una marca en settings.
"""

import io
import os
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-por-defecto")

from backend import database  # noqa: E402

CARPETA = tempfile.mkdtemp(prefix="pordefecto-")
database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
database.init_db()

from backend import settings as ajustes  # noqa: E402
from backend import schedule as horarios  # noqa: E402
from backend import devices as equipos  # noqa: E402
from backend import organizations  # noqa: E402


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


_organizacion = None


def organizacion():
    """
    Una organizacion de verdad sobre la que probar.

    ensure_default_organization() devuelve None en una base recien
    creada -no hay nada que migrar-, asi que aqui se crea una.
    """

    global _organizacion

    if _organizacion is None:

        _organizacion = organizations.ensure_default_organization()

        if _organizacion is None:
            creada = organizations.create_organization("Prueba")
            _organizacion = (
                creada["id"] if isinstance(creada, dict) else creada
            )

    return _organizacion


def poner(clave, valor, org):

    conexion = database.get_connection()

    try:
        conexion.execute(
            "INSERT OR REPLACE INTO organization_settings "
            "(organization_id, key, value) VALUES (?, ?, ?)",
            (org, clave, valor)
        )
        conexion.commit()

    finally:
        # Siempre. Una conexion que se queda abierta por una
        # excepcion deja la base bloqueada, y las pruebas
        # siguientes fallan por una causa que no es la suya.
        conexion.close()


# ==================================================================
# 1. QUE NACE ENCENDIDO
# ==================================================================

def test_la_empresa_dice_con_que_nace_un_equipo():

    org = organizacion()

    porque = ajustes.grabacion_por_defecto(org)

    comprobar("De fabrica, los equipos nacen grabando",
              porque is not None, str(porque))

    if porque is None:
        return

    comprobar("De lunes a viernes",
              porque["days"] == [0, 1, 2, 3, 4], str(porque["days"]))

    comprobar("De 8 a 5",
              porque["start_time"] == "08:00"
              and porque["end_time"] == "17:00",
              "%s-%s" % (porque["start_time"], porque["end_time"]))

    comprobar("Y con el horario activado",
              porque["enabled"] is True)


def test_una_empresa_puede_decir_que_no():
    """
    No todas quieren grabar a sus empleados, y es una decision suya.
    """

    org = organizacion()

    poner("recording_default_enabled", "false", org)

    comprobar("Apagado el ajuste, no se impone ningun horario",
              ajustes.grabacion_por_defecto(org) is None)

    poner("recording_default_enabled", "true", org)

    comprobar("Y volviendo a encenderlo, vuelve",
              ajustes.grabacion_por_defecto(org) is not None)


def test_la_empresa_pone_sus_propias_horas():

    org = organizacion()

    poner("recording_default_start", "07:30", org)
    poner("recording_default_end", "15:00", org)
    poner("recording_default_days", "0,1,2,3,4,5", org)

    porque = ajustes.grabacion_por_defecto(org)

    comprobar("Se respetan las horas de la empresa",
              porque["start_time"] == "07:30"
              and porque["end_time"] == "15:00",
              str(porque))

    comprobar("Y sus dias, sabado incluido",
              porque["days"] == [0, 1, 2, 3, 4, 5], str(porque["days"]))

    # Se deja como estaba para no condicionar a las demas pruebas
    poner("recording_default_start", "08:00", org)
    poner("recording_default_end", "17:00", org)
    poner("recording_default_days", "0,1,2,3,4", org)


def test_unos_dias_sin_sentido_no_dejan_al_equipo_sin_horario():
    """
    El valor lo escribe una persona. Si se equivoca, el equipo tiene
    que nacer con algo razonable, no sin nada.
    """

    org = organizacion()

    poner("recording_default_days", "lunes y martes", org)

    porque = ajustes.grabacion_por_defecto(org)

    comprobar("Con dias ilegibles se cae a lunes-viernes",
              porque is not None and porque["days"] == [0, 1, 2, 3, 4],
              str(porque))

    poner("recording_default_days", "0,1,2,3,4", org)


# ==================================================================
# 2. QUE SE PUEDE APAGAR, Y SE QUEDA APAGADO
# ==================================================================

def test_apagarlo_en_un_equipo_no_lo_deshace_nada():
    """
    La mitad que de verdad importa.

    Si el ajuste de empresa se reaplicara cada cierto tiempo, apagar
    un equipo duraria hasta el siguiente ciclo, y relacionar una
    cosa con la otra costaria semanas.
    """

    org = organizacion()

    from backend.models import DeviceRegister

    equipos.register_device(DeviceRegister(
        device_id="eq-apagable", hostname="PC-APAGABLE",
        operating_system="Windows-11", ip_address="10.0.0.9"
    ))

    # Nace como nace un equipo nuevo
    porque = ajustes.grabacion_por_defecto(org)
    horarios.set_schedule("eq-apagable", porque)
    equipos.set_continuous_recording("eq-apagable", True)

    comprobar("Nace con horario",
              horarios.get_schedule("eq-apagable")["enabled"] is True)

    comprobar("Y con el interruptor encendido",
              equipos.get_continuous_recording("eq-apagable") == 1,
              str(equipos.get_continuous_recording("eq-apagable")))

    # --- el tecnico lo apaga ---
    equipos.set_continuous_recording("eq-apagable", False)

    horarios.set_schedule("eq-apagable", dict(porque, enabled=False))

    comprobar("Se puede apagar el horario",
              horarios.get_schedule("eq-apagable")["enabled"] is False)

    comprobar("Y el interruptor",
              not equipos.get_continuous_recording("eq-apagable"))

    # --- y nada lo vuelve a encender ---
    #
    # Se consulta el ajuste de la empresa otra vez, que es lo que
    # haria cualquier tarea de fondo. Mirarlo no puede cambiar nada.
    ajustes.grabacion_por_defecto(org)

    comprobar("Consultar el ajuste de empresa no lo reenciende",
              horarios.get_schedule("eq-apagable")["enabled"] is False
              and not equipos.get_continuous_recording("eq-apagable"))


def test_el_valor_por_defecto_solo_toca_a_los_nuevos():
    """
    Un equipo que YA existe no se ve afectado por cambiar el ajuste
    de la empresa. Si no, cambiar el horario de la casa reescribiria
    el de todos los equipos que alguien hubiera ajustado a mano.
    """

    org = organizacion()

    from backend.models import DeviceRegister

    equipos.register_device(DeviceRegister(
        device_id="eq-viejo", hostname="PC-VIEJO",
        operating_system="Windows-11", ip_address="10.0.0.10"
    ))

    horarios.set_schedule("eq-viejo", {
        "enabled": True, "start_time": "22:00", "end_time": "06:00",
        "days": [5, 6], "timezone": "local"
    })

    # La empresa cambia su valor por defecto
    poner("recording_default_start", "09:00", org)
    ajustes.grabacion_por_defecto(org)

    suyo = horarios.get_schedule("eq-viejo")

    comprobar("El horario propio de un equipo no se pisa",
              suyo["start_time"] == "22:00", str(suyo["start_time"]))

    comprobar("Ni sus dias",
              sorted(suyo["days"]) == [5, 6], str(suyo["days"]))

    poner("recording_default_start", "08:00", org)


# ==================================================================
# 3. EL ALTA
# ==================================================================

def test_el_alta_aplica_el_horario_y_no_se_cae_si_falla():

    SERVIDOR = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()

    alta = SERVIDOR.split("device_id, agent_token = enroll_device(", 1)[1][:2200]

    comprobar("El alta consulta el valor de la empresa",
              "ajustes_mod.grabacion_por_defecto(organizacion)" in alta)

    comprobar("Y aplica horario e interruptor",
              "recording_schedule.set_schedule(device_id" in alta
              and "set_continuous_recording(device_id, True)" in alta)

    # Si esto fallara, el alta NO puede caerse: un equipo dado de
    # alta sin horario se arregla con dos clics, pero un alta que
    # revienta deja la maquina fuera del sistema.
    comprobar("Un fallo aqui no tumba el alta",
              "except Exception as problema:" in alta
              and "No se pudo aplicar el horario por defecto" in alta)

    comprobar("La organizacion sale de la credencial, no del cuerpo",
              "organization_for_token" in SERVIDOR)


def test_el_boton_de_aplicar_a_los_que_ya_estaban():
    """
    El valor por defecto solo toca a los equipos NUEVOS, asi que los
    que ya existian necesitan algo expreso. Expreso quiere decir que
    pasa cuando alguien lo pulsa, nunca por su cuenta: lo contrario
    convertiria el ajuste de la empresa en algo que reescribe
    decisiones ajenas cada cierto tiempo.
    """

    SERVIDOR = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()
    JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
    HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()

    comprobar("Existe el endpoint",
              "/api/recording/schedule/apply-default" in SERVIDOR)

    cuerpo = SERVIDOR.split(
        "async def aplicar_horario_por_defecto(", 1
    )[1][:3600]

    comprobar("Hace falta poder administrar grabaciones",
              'require_permission(request, "recordings.manage")' in cuerpo)

    # Lo mas importante: por omision NO pisa al que ya tiene horario
    # propio. El turno de noche que alguien configuro a mano tiene su
    # razon de ser, y rellenar huecos es una cosa y borrar el trabajo
    # de otro es otra.
    comprobar("Por omision respeta a quien ya tiene horario",
              'actual.get("enabled") and not pisar' in cuerpo)

    comprobar("Y pisarlos hay que pedirlo aparte",
              'data.get("incluir_configurados")' in cuerpo)

    comprobar("Se cuenta lo aplicado Y lo respetado",
              '"aplicados": len(aplicados)' in cuerpo
              and '"respetados": len(respetados)' in cuerpo)

    comprobar("Un equipo dado de baja no se toca",
              'equipo.get("decommissioned_at")' in cuerpo)

    comprobar("No se cruza de empresa",
              'equipo.get("organization_id") != organizacion' in cuerpo)

    comprobar("Se avisa al equipo si esta conectado",
              '"set_schedule:" + json.dumps(horario)' in cuerpo)

    comprobar("Queda en la auditoria",
              '"recording.schedule.apply_default"' in cuerpo)

    # --- y el panel ---

    comprobar("Hay boton en el panel",
              'id="aplicar-horario-defecto"' in HTML)

    comprobar("Que dice que no toca a los que tienen horario propio",
              "No toca" in HTML)

    comprobar("El panel pide la version que NO pisa",
              "incluir_configurados: false" in JS)

    comprobar("Y cuenta cuantos se respetaron",
              "datos.respetados" in JS)


def test_la_clave_nueva_se_puede_guardar_desde_el_panel():
    """
    Hay lista blanca de ajustes escribibles. Una clave que no este
    en ella se rechaza, asi que anadir el valor por defecto sin
    anadirlo a la lista lo dejaria de solo lectura.
    """

    for clave in ("recording_default_enabled",
                  "recording_default_start",
                  "recording_default_end",
                  "recording_default_days",
                  "recording_default_timezone"):

        comprobar("Se puede guardar %s" % clave,
                  clave in ajustes.ALLOWED_SETTINGS)


# ==================================================================

def principal():

    print()
    print("=" * 66)
    print("UN EQUIPO NUEVO NACE GRABANDO")
    print("=" * 66)
    print()

    database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
    database.init_db()

    pruebas = [
        test_la_empresa_dice_con_que_nace_un_equipo,
        test_una_empresa_puede_decir_que_no,
        test_la_empresa_pone_sus_propias_horas,
        test_unos_dias_sin_sentido_no_dejan_al_equipo_sin_horario,
        test_apagarlo_en_un_equipo_no_lo_deshace_nada,
        test_el_valor_por_defecto_solo_toca_a_los_nuevos,
        test_el_alta_aplica_el_horario_y_no_se_cae_si_falla,
        test_el_boton_de_aplicar_a_los_que_ya_estaban,
        test_la_clave_nueva_se_puede_guardar_desde_el_panel,
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      "%s: %s" % (type(error).__name__, error))

    fallos = 0

    for nombre, bien, detalle in resultados:

        if bien:
            print("  ok     %s" % nombre)

        else:
            fallos += 1
            print("  FALLA  %s" % nombre)

            if detalle:
                print("         %s" % detalle)

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos,
                                    len(resultados)))

    database.DATABASE_PATH = os.path.join(
        tempfile.mkdtemp(prefix="apartado-"), "vacia.db"
    )

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
