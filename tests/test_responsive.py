"""
Que el panel y la pagina publica quepan en un telefono.

    .venv\\Scripts\\python tests/test_responsive.py

Por que esta prueba se escribe de otra manera
---------------------------------------------

La version anterior de la comprobacion del pie de tarjeta daba verde
mientras el texto se salia del recuadro en la pantalla. Comprobaba que
la regla ESTUVIERA escrita:

    ".vy-metrica-pie .vy-punto" in CSS      -> cierto

y lo estaba. Lo que no comprobaba es que alcanzara a algo: el elemento
real cuelga de un div sin esa clase, asi que la regla no se aplicaba a
nada. Una prueba que mira la hoja de estilos por su cuenta no puede
distinguir eso.

Asi que aqui se parte SIEMPRE del marcado. Se localiza el elemento de
verdad en index.html, se leen las clases que lleva encima, y se exige
que algun selector escrito lo alcance. Para eso hay un comparador de
selectores muy reducido —clases, ids y descendencia— que es lo unico
que usan estas hojas.

Lo que esto NO puede hacer
--------------------------

No hay navegador. No se mide un solo pixel, no se detecta un
solapamiento ni se sabe si algo se ve bien. Se comprueba que las
reglas existan Y alcancen al marcado; que el resultado sea agradable
a 320px hay que mirarlo con los ojos. Al final se imprime la lista de
lo que queda por validar a mano.
"""

import io
import re
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

PANEL = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()
INICIO = io.open(RAIZ / "frontend" / "inicio.html", encoding="utf-8").read()
CSS = io.open(RAIZ / "frontend" / "veyra.css", encoding="utf-8").read()


ANCHOS = (320, 375, 390, 430, 768, 1024, 1366)


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ==================================================================
# LEER LA HOJA DE ESTILOS
# ==================================================================

def _sin_comentarios(texto):
    return re.sub(r"/\*.*?\*/", "", texto, flags=re.S)


def reglas_para(ancho):
    """
    Selectores que estan en vigor a este ancho de pantalla.

    Solo se interpretan las consultas 'max-width', que son las unicas
    que usa esta hoja. Lo que este dentro de una que no se cumpla a
    este ancho, no cuenta.
    """

    texto = _sin_comentarios(CSS)

    encontrados = []

    posicion = 0

    while True:

        inicio = texto.find("@media", posicion)

        if inicio == -1:
            break

        # La condicion de la consulta
        abre = texto.find("{", inicio)
        condicion = texto[inicio + 6:abre]

        # Su bloque, contando llaves
        nivel = 0
        fin = abre

        for indice in range(abre, len(texto)):

            if texto[indice] == "{":
                nivel += 1

            elif texto[indice] == "}":
                nivel -= 1

                if nivel == 0:
                    fin = indice
                    break

        bloque = texto[abre + 1:fin]

        limite = re.search(r"max-width:\s*(\d+)px", condicion)

        if limite and ancho <= int(limite.group(1)):
            encontrados.append(bloque)

        elif not limite:
            # prefers-reduced-motion y similares: no dependen del ancho
            encontrados.append(bloque)

        texto = texto[:inicio] + " " * (fin - inicio + 1) + texto[fin + 1:]

        posicion = inicio

    # Lo que queda fuera de toda consulta vale a cualquier ancho
    encontrados.append(texto)

    selectores = []

    for bloque in encontrados:

        for trozo in re.findall(r"([^{}]+)\{([^{}]*)\}", bloque):

            for selector in trozo[0].split(","):

                selector = selector.strip()

                if selector and not selector.startswith("@"):
                    selectores.append((selector, trozo[1]))

    return selectores


# ==================================================================
# LEER EL MARCADO
# ==================================================================

def cadena_de_clases(html, marca):
    """
    Clases e ids del elemento que contiene 'marca' y de sus padres.

    Devuelve una lista de conjuntos, del elemento hacia arriba. No es
    un analizador de HTML completo: cuenta etiquetas abiertas y
    cerradas hasta la posicion de la marca, que para este marcado
    —bien indentado y sin etiquetas sueltas— basta.
    """

    corte = html.index(marca)

    pila = []

    for encaje in re.finditer(r"<(/?)([a-zA-Z0-9]+)([^>]*)>", html[:corte]):

        cierre, etiqueta, atributos = encaje.groups()

        if etiqueta.lower() in ("br", "img", "input", "meta", "link",
                                "use", "path", "hr", "source"):
            continue

        if atributos.rstrip().endswith("/"):
            continue

        if cierre:

            if pila:
                pila.pop()

            continue

        señas = set()

        clases = re.search(r'class="([^"]*)"', atributos)

        if clases:
            señas.update(clases.group(1).split())

        identificador = re.search(r'id="([^"]*)"', atributos)

        if identificador:
            señas.add("#" + identificador.group(1))

        señas.add(etiqueta.lower())

        pila.append(señas)

    # El propio elemento: la etiqueta ENTERA que contiene la marca.
    #
    # Hay que ir hasta el '>' de cierre, no quedarse en la marca: en
    # este marcado los atributos van uno por linea y la clase suele
    # estar DESPUES del id por el que se busca.
    abre = html.rfind("<", 0, corte + 1)
    cierra = html.find(">", corte)

    propio = html[abre + 1:cierra]

    señas = set()

    clases = re.search(r'class="([^"]*)"', propio)

    if clases:
        señas.update(clases.group(1).split())

    identificador = re.search(r'id="([^"]*)"', propio)

    if identificador:
        señas.add("#" + identificador.group(1))

    etiqueta = re.match(r"\s*([a-zA-Z0-9]+)", propio)

    if etiqueta:
        señas.add(etiqueta.group(1).lower())

    return [señas] + pila[::-1]


def alcanza(selector, cadena):
    """
    True si este selector se aplica al elemento descrito por 'cadena'.

    'cadena' viene del elemento hacia arriba. Se admite descendencia
    (espacio), clases, ids, etiquetas y :not(...) —que se ignora,
    porque ignorarlo solo puede hacer que la prueba sea mas exigente,
    nunca menos.
    """

    selector = re.sub(r":not\([^)]*\)", "", selector)
    selector = re.sub(r"::?[a-z-]+(\([^)]*\))?", "", selector)
    selector = selector.replace(">", " ").strip()

    if not selector:
        return False

    partes = [p for p in selector.split() if p]

    if not partes:
        return False

    def encaja(parte, señas):

        for pieza in re.findall(r"[.#]?[A-Za-z0-9_-]+", parte):

            if pieza.startswith("."):

                if pieza[1:] not in señas:
                    return False

            elif pieza.startswith("#"):

                if pieza not in señas:
                    return False

            else:

                if pieza.lower() not in señas:
                    return False

        return True

    # La ultima parte describe al elemento
    if not encaja(partes[-1], cadena[0]):
        return False

    # Las anteriores, a algun antepasado, en orden
    nivel = 1

    for parte in reversed(partes[:-1]):

        while nivel < len(cadena) and not encaja(parte, cadena[nivel]):
            nivel += 1

        if nivel >= len(cadena):
            return False

        nivel += 1

    return True


def especificidad(selector):
    """
    (ids, clases, etiquetas), como las cuenta el navegador.

    Hace falta de verdad y no es un adorno: ".summary-card .vy-punto"
    esta ANTES en la hoja que ".vy-punto", y aun asi gana. Sin esto la
    prueba daba por perdido el pie de la tarjeta cuando en pantalla se
    ve bien.
    """

    limpio = re.sub(r":not\(([^)]*)\)", r"  ", selector)
    limpio = re.sub(r"::[a-z-]+", " ", limpio)

    ids = len(re.findall(r"#[A-Za-z0-9_-]+", limpio))

    clases = len(re.findall(r"\.[A-Za-z0-9_-]+", limpio))
    clases += len(re.findall(r":[a-z-]+(?:\([^)]*\))?", limpio))
    clases += len(re.findall(r"\[[^\]]*\]", limpio))

    sin_señas = re.sub(r"[.#][A-Za-z0-9_-]+", " ", limpio)
    sin_señas = re.sub(r":[a-z-]+(?:\([^)]*\))?", " ", sin_señas)

    etiquetas = len(re.findall(r"[a-z][a-z0-9]*", sin_señas))

    return (ids, clases, etiquetas)


def propiedad_de(marca, propiedad, ancho, html=None):
    """
    Valor que gana para esta propiedad, en este elemento, a este ancho.

    Se recorren los selectores en vigor, se descartan los que no
    alcanzan al elemento, y entre los que quedan manda la cascada:
    !important primero, luego la especificidad, y a igualdad, el
    ultimo escrito.
    """

    cadena = cadena_de_clases(html or PANEL, marca)

    mejor = None
    orden = 0

    for selector, cuerpo in reglas_para(ancho):

        orden += 1

        if not alcanza(selector, cadena):
            continue

        encaje = re.search(
            r"(?:^|;)\s*" + re.escape(propiedad) + r"\s*:([^;]*)",
            cuerpo
        )

        if not encaje:
            continue

        bruto = encaje.group(1).strip()

        peso = (
            1 if "!important" in bruto else 0,
            especificidad(selector),
            orden
        )

        if mejor is None or peso > mejor[0]:
            mejor = (peso, bruto.replace("!important", "").strip())

    return mejor[1] if mejor else None


# ==================================================================
# LAS COMPROBACIONES
# ==================================================================

def test_el_pie_de_la_tarjeta_en_riesgo_puede_partirse():
    """
    "1 equipo sin datos de RAM/disco" se salia del recuadro.

    Es el caso que la prueba anterior dejo pasar. Ahora se parte del
    elemento real y se exige que la regla LE LLEGUE.
    """

    for ancho in ANCHOS:

        valor = propiedad_de('id="nodata-devices"', "white-space", ancho)

        comprobar(
            f"A {ancho}px el pie de 'En riesgo' puede partirse",
            valor == "normal",
            f"white-space resuelto: {valor!r}"
        )

    # Y los puntos de una palabra conservan su nowrap
    general = CSS.split(chr(10) + ".vy-punto {", 1)[1].split("}", 1)[0]

    comprobar("Un punto suelto sigue sin partirse",
              "white-space: nowrap" in general, general)


def test_las_tarjetas_de_resumen_no_van_en_dos_columnas_en_un_movil():
    """
    Cinco tarjetas a dos columnas dejan 128px cada una a 320px.

    No cabe el numero grande con su icono al lado.
    """

    estrechos = (320, 375, 390, 430)

    for ancho in estrechos:

        valor = propiedad_de('id="stats-section"', "grid-template-columns",
                             ancho)

        comprobar(
            f"A {ancho}px las tarjetas van en una columna",
            valor == "1fr",
            f"grid-template-columns: {valor!r}"
        )

    # Y en escritorio NO se toca: manda la clase de Tailwind
    for ancho in (1024, 1366):

        valor = propiedad_de('id="stats-section"', "grid-template-columns",
                             ancho)

        comprobar(
            f"A {ancho}px no se impone ninguna columna desde la hoja",
            valor is None,
            f"grid-template-columns: {valor!r}"
        )


def test_el_contenido_gana_ancho_en_un_telefono():
    """main lleva p-6: 48px de los 320 de un telefono pequeno."""

    for ancho in (320, 375, 390, 430):

        valor = propiedad_de('<main class="p-6">', "padding", ancho)

        comprobar(f"A {ancho}px el contenido reduce su relleno",
                  valor == "14px", f"padding: {valor!r}")

    for ancho in (768, 1024, 1366):

        valor = propiedad_de('<main class="p-6">', "padding", ancho)

        comprobar(f"A {ancho}px el relleno de escritorio no se toca",
                  valor is None, f"padding: {valor!r}")


def test_la_cabecera_publica_cabe_en_un_telefono():
    """A 390px se salia por la derecha y arrastraba la pagina entera."""

    for ancho in (320, 375, 390, 430):

        valor = propiedad_de('<header class="vy-publico-nav">', "flex-wrap",
                             ancho, html=INICIO)

        comprobar(f"A {ancho}px la cabecera publica puede partirse",
                  valor == "wrap", f"flex-wrap: {valor!r}")

    for ancho in (1024, 1366):

        valor = propiedad_de('<header class="vy-publico-nav">', "flex-wrap",
                             ancho, html=INICIO)

        comprobar(f"A {ancho}px sigue en una sola fila",
                  valor != "wrap", f"flex-wrap: {valor!r}")

    # Y sin esconder nada: los dos botones siguen ahi
    cabecera = INICIO.split("<header", 1)[1].split("</header>", 1)[0]

    comprobar("Iniciar sesion sigue en la cabecera",
              "Iniciar sesión" in cabecera)

    comprobar("Solicitar acceso sigue en la cabecera",
              "Solicitar acceso" in cabecera)


def test_la_barra_lateral_es_un_cajon_en_movil():
    """Por debajo de 768px se retira, pero con un boton que la abre."""

    for ancho in (320, 375, 390, 430, 767):

        valor = propiedad_de('class="vy-sidebar', "transform", ancho)

        comprobar(f"A {ancho}px la barra lateral se retira",
                  valor == "translateX(-100%)", f"transform: {valor!r}")

    for ancho in (768, 1024, 1366):

        valor = propiedad_de('class="vy-sidebar', "transform", ancho)

        comprobar(f"A {ancho}px la barra lateral sigue a la vista",
                  valor is None, f"transform: {valor!r}")

    # El boton que la abre tiene que existir en el marcado
    comprobar("Hay un boton para abrir el cajon",
              'id="vy-menu-boton"' in PANEL)

    comprobar("Y uno para cerrarlo",
              "vy-cerrar-cajon" in PANEL)


def test_todas_las_tablas_tienen_su_propio_desplazamiento():
    """
    Una tabla ancha debe desplazarse ELLA, no la pagina.

    Es la diferencia entre poder leer una columna de la derecha y que
    se mueva la interfaz entera al intentarlo.
    """

    posicion = 0

    tablas = 0

    while True:

        posicion = PANEL.find("<table", posicion)

        if posicion == -1:
            break

        tablas += 1

        cadena = cadena_de_clases(PANEL, PANEL[posicion:posicion + 7])

        tiene = any("overflow-x-auto" in señas for señas in cadena)

        comprobar(
            f"La tabla {tablas} se desplaza por su cuenta",
            tiene,
            "ningun antepasado lleva overflow-x-auto"
        )

        posicion += 6

    comprobar("Se han revisado todas las tablas del panel",
              tablas == 6, f"encontradas: {tablas}")


def test_los_objetivos_tactiles_son_comodos():
    """Un dedo no acierta en un boton de 24px."""

    bloque = CSS.split("@media (pointer: coarse) {", 1)

    comprobar("Hay una regla para pantallas tactiles", len(bloque) == 2)

    if len(bloque) == 2:

        cuerpo = bloque[1].split(chr(10) + "}", 1)[0]

        comprobar("Con una altura minima comoda",
                  "min-height: 40px" in cuerpo, cuerpo[:200])

        for clase in (".vy-btn", ".vy-btn-icono", ".vy-nav-item"):
            comprobar(f"Alcanza a {clase}", clase in cuerpo)


def test_se_respeta_el_area_segura():
    """La muesca y la barra inferior de un telefono."""

    comprobar("La pagina lo pide en su viewport",
              "viewport-fit=cover" in PANEL)

    comprobar("Y la publica tambien",
              "viewport-fit=cover" in INICIO)

    comprobar("La hoja usa env() para el recorte",
              "env(safe-area-inset-left)" in CSS)

    # Y no a costa del escritorio: el relleno de la cabecera a 1366
    # tiene que seguir siendo el de siempre
    valor = propiedad_de('class="vy-header"', "padding-left", 1366)

    comprobar("En escritorio no cambia el relleno de la cabecera",
              valor is None, f"padding-left: {valor!r}")


def test_no_se_ha_ocultado_ningun_control_para_ganar_sitio():
    """
    La salida facil seria 'display:none' en lo que estorba.

    Se comprueba que los bloques nuevos de telefono no escondan nada.
    """

    for marca in ("@media (max-width: 560px) {", "@media (max-width: 480px) {"):

        if marca not in CSS:
            comprobar(f"Existe {marca}", False)
            continue

        cuerpo = CSS.split(marca, 1)[1].split(chr(10) + "}", 1)[0]

        comprobar(f"{marca} no esconde nada",
                  "display: none" not in cuerpo,
                  cuerpo[:300])


def test_el_comparador_de_selectores_funciona():
    """
    La prueba se apoya entera en 'alcanza'. Si se equivoca, todo lo
    demas miente. Asi que se comprueba contra casos conocidos.
    """

    cadena = [
        {"vy-punto", "vy-punto-neutro", "#nodata-devices", "span"},
        {"min-w-0", "div"},
        {"summary-card", "vy-card", "#card-warnings", "div"},
        {"#stats-section", "grid", "section"}
    ]

    comprobar("Alcanza por clase directa",
              alcanza(".vy-punto", cadena))

    comprobar("Alcanza por id",
              alcanza("#nodata-devices", cadena))

    comprobar("Alcanza por descendencia",
              alcanza(".summary-card .vy-punto", cadena))

    comprobar("Alcanza saltando niveles",
              alcanza("#stats-section .vy-punto", cadena))

    comprobar("NO alcanza si falta el antepasado",
              not alcanza(".vy-metrica-pie .vy-punto", cadena))

    comprobar("NO alcanza si la clase no esta",
              not alcanza(".vy-tabla", cadena))

    comprobar("El orden de la descendencia importa",
              not alcanza(".vy-punto .summary-card", cadena))

    comprobar("Una etiqueta cuenta como seña",
              alcanza("span", cadena))

    comprobar("Clase y etiqueta juntas",
              alcanza("main.p-6", [{"main", "p-6"}]))

    comprobar("Clase y etiqueta juntas, si falta una no",
              not alcanza("main.p-6", [{"main"}]))


# ==================================================================

def principal():

    pruebas = [
        test_el_comparador_de_selectores_funciona,
        test_el_pie_de_la_tarjeta_en_riesgo_puede_partirse,
        test_las_tarjetas_de_resumen_no_van_en_dos_columnas_en_un_movil,
        test_el_contenido_gana_ancho_en_un_telefono,
        test_la_cabecera_publica_cabe_en_un_telefono,
        test_la_barra_lateral_es_un_cajon_en_movil,
        test_todas_las_tablas_tienen_su_propio_desplazamiento,
        test_los_objetivos_tactiles_son_comodos,
        test_se_respeta_el_area_segura,
        test_no_se_ha_ocultado_ningun_control_para_ganar_sitio
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:140])

    print()
    print("=" * 66)
    print("RESPONSIVE: 320 / 375 / 390 / 430 / 768 / 1024 / 1366")
    print("=" * 66)
    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        if bien:
            continue

        fallos += 1

        print(f"  FALLA  {nombre}")

        if detalle:
            print(f"         {detalle}")

    if not fallos:
        print("  todo correcto")

    print()
    print(f"{len(resultados) - fallos}/{len(resultados)} comprobaciones")

    print()
    print("-" * 66)
    print("LO QUE ESTA PRUEBA NO PUEDE DECIR")
    print("-" * 66)
    print("""
No hay navegador: no se mide un pixel ni se detecta un solapamiento.
Queda por mirar con los ojos, en un telefono de verdad:

  - que ningun texto se monte sobre otro a 320px
  - que las seis vistas se desplacen sin arrastrar la pagina a los lados
  - que el cajon se abra, se cierre y no tape el contenido
  - que los desplegables y el calendario de fechas no salgan de pantalla
  - que en horizontal la cabecera no se coma la mitad del alto
  - que los botones de grabar y detener se pulsen comodos con el pulgar
""")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
