r"""
Las credenciales de instalacion sin usar se pueden retirar.

    .venv\Scripts\python tests/test_revocar_credenciales.py

Una credencial de instalacion autoriza dar de alta un equipo en la
organizacion. No caduca sola si no se le puso fecha, y vive hasta que
alguien la retira.

El servidor sabia revocarlas desde el principio
-DELETE /api/enrollment-tokens/{id}- pero el panel solo las listaba.
Sin un boton, la unica via era la API, asi que en la practica no se
retiraba ninguna.

El resultado medido en produccion: 21 credenciales creadas, 20
activas y 10 sin estrenar, algunas de hace mas de un mes. Diez llaves
sueltas para dar de alta equipos en la organizacion, ninguna
necesaria.

Lo que se comprueba aqui:

  revocar funciona       y deja rastro: la fila no se borra, se
                         desactiva, asi que se conserva cuando se
                         creo y si llego a usarse

  deja de servir         una credencial revocada ya no autoriza
                         ningun alta. Esto es lo unico que de verdad
                         importa, y por eso se prueba contra la
                         funcion que decide, no contra el listado

  no afecta a los que    revocar la credencial con la que se dio de
  ya estan dados de alta alta un equipo no desconecta ese equipo:
                         cada uno tiene su token individual

Lo ultimo es lo que hace que retirarlas sea seguro, y es justo lo que
el aviso del panel le promete a quien pulsa. Si fuera falso, el boton
seria una trampa.
"""

import io
import os
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))

os.environ.setdefault(
    "AUTH_SECRET_KEY", "clave-solo-para-credenciales-" + "x" * 20
)

from backend import database  # noqa: E402

CARPETA = tempfile.mkdtemp(prefix="credenciales-")
database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
database.init_db()

from backend import enrollment  # noqa: E402
from backend import organizations  # noqa: E402


PANEL = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


_org = None


def organizacion():

    global _org

    if _org is None:

        _org = organizations.ensure_default_organization()

        if _org is None:
            creada = organizations.create_organization("Prueba")
            _org = creada["id"] if isinstance(creada, dict) else creada

    return _org


def prueba_revocar_una_sin_usar():

    ficha, valor = enrollment.create_token(organizacion(), label="K9")

    comprobar("la credencial nace usable",
              enrollment.get_token(ficha["id"])["usable"])

    comprobar("y autoriza un alta",
              enrollment.organization_for_token(valor) == organizacion())

    enrollment.revoke_token(ficha["id"])

    despues = enrollment.get_token(ficha["id"])

    comprobar("revocada deja de ser usable", not despues["usable"])

    comprobar("y queda inactiva", not despues["active"])

    # Lo unico que de verdad importa: que no autorice mas altas.
    comprobar(
        "ya NO autoriza ningun alta",
        enrollment.organization_for_token(valor) is None
    )


def prueba_no_se_borra_la_fila():
    """
    Revocar no es borrar: se conserva cuando se creo y si se uso.

    Importa porque una credencial es una autorizacion, y las
    autorizaciones se auditan. Borrar la fila dejaria sin explicacion
    los equipos que se dieron de alta con ella.
    """

    ficha, valor = enrollment.create_token(organizacion(), label="rastro")

    identificador = ficha["id"]

    enrollment.revoke_token(identificador)

    quedo = enrollment.get_token(identificador)

    comprobar("la ficha sigue existiendo", quedo is not None)

    comprobar("conserva su etiqueta",
              quedo and quedo["label"] == "rastro", quedo)

    comprobar("y conserva cuando se creo",
              quedo and bool(quedo["created_at"]))

    comprobar("y sigue apareciendo en el listado",
              any(t["id"] == identificador
                  for t in enrollment.list_tokens(organizacion())))


def prueba_revocar_no_desconecta_equipos():
    """
    La promesa que el panel le hace a quien pulsa el boton.

    Un equipo dado de alta usa su token INDIVIDUAL, que emite
    issue_agent_token y no tiene nada que ver con la credencial de
    alta. Si revocar la credencial tumbara los equipos que se dieron
    de alta con ella, el boton seria una trampa.
    """

    from backend import devices as equipos

    ficha, valor = enrollment.create_token(organizacion(), label="con uso")

    comprobar("la credencial sirve para dar de alta",
              enrollment.organization_for_token(valor) == organizacion())

    # El equipo recibe SU token, que es otro. La fila se crea
    # directamente: aqui lo que se prueba es la relacion entre las
    # dos credenciales, no el alta, que tiene sus propias pruebas.
    equipo = "equipodeprueba01"

    conexion = database.get_connection()

    try:
        conexion.execute(
            "INSERT OR IGNORE INTO devices "
            "(device_id, hostname, status, organization_id) "
            "VALUES (?, ?, 'online', ?)",
            (equipo, "PRUEBA", organizacion())
        )
        conexion.commit()

    finally:
        conexion.close()

    propio = equipos.issue_agent_token(equipo)

    comprobar("el equipo tiene su token individual",
              bool(propio), propio)

    enrollment.revoke_token(ficha["id"])

    comprobar("tras revocar, la credencial no sirve para mas altas",
              enrollment.organization_for_token(valor) is None)

    comprobar(
        "pero el token individual del equipo sigue valiendo",
        equipos.get_device_id_for_token(propio) == equipo,
        equipos.get_device_id_for_token(propio)
    )


def prueba_el_panel_tiene_el_boton():

    comprobar("existe la funcion que revoca",
              "async function revocarCredencial(" in PANEL)

    comprobar("llama al endpoint con DELETE",
              'method: "DELETE"' in PANEL
              and '"/api/enrollment-tokens/" + encodeURIComponent(' in PANEL)

    comprobar("hay un boton Revocar en cada fila",
              'borrar.textContent = "Revocar";' in PANEL)

    comprobar("y pide confirmacion antes",
              "window.confirm(" in PANEL)

    comprobar("tras revocar se recarga la lista",
              "await cargarCredencialesDeInstalacion();" in PANEL)


def prueba_el_panel_distingue_las_sin_usar():
    """
    'sin usar' es un aviso, no un dato. '0 uso(s)' se lee como una
    estadistica y no mueve a nadie a retirarla.
    """

    comprobar("se dice 'sin usar' con esas palabras",
              '"sin usar"' in PANEL)

    comprobar("y se marca en otro color",
              "text-amber-600" in PANEL)

    comprobar("se enseña la fecha de creacion de las no usadas",
              "creada el " in PANEL)


def prueba_el_identificador_va_escapado():
    """
    Va en la URL. Es un numero, pero construir rutas concatenando sin
    escapar es como se cuela la mitad de los fallos de este tipo.
    """

    comprobar(
        "el id se escapa al meterlo en la ruta",
        'encodeURIComponent(credencial.id)' in PANEL
    )


def principal():

    prueba_revocar_una_sin_usar()
    prueba_no_se_borra_la_fila()
    prueba_revocar_no_desconecta_equipos()
    prueba_el_panel_tiene_el_boton()
    prueba_el_panel_distingue_las_sin_usar()
    prueba_el_identificador_va_escapado()

    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        if bien:
            print("  bien   %s" % nombre)
        else:
            fallos += 1
            print("  FALLA  %s" % nombre)

        if detalle:
            print("         %s" % detalle)

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos,
                                    len(resultados)))

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
