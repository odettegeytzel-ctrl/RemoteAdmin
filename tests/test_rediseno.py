"""
El rediseno: identidad, iconos, temas y navegacion adaptable.

    .venv\\Scripts\\python tests/test_rediseno.py

No hay navegador en este entorno, asi que esto no comprueba como se
VE la pagina; eso hay que mirarlo a ojo. Comprueba lo que si se puede
comprobar sin uno:

  - que las paginas se sirven y el HTML cierra bien,
  - que no quedan emojis haciendo de iconos,
  - que cada <use href="#i-..."> apunta a un simbolo que existe,
  - que los dos temas definen todas las variables,
  - que la barra lateral tiene alternativa en movil,
  - que los enlaces de la pagina publica llevan a algun sitio real,
  - y que no se cuela ningun dato inventado presentado como real.

Base temporal: no se toca la real.
"""

import io
import os
import re
import sys

from html.parser import HTMLParser
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

os.environ.setdefault("REMOTEADMIN_PUBLIC_URL", "https://panel.ejemplo.com")

# IMPORTANTE: el andamiaje va PRIMERO.
#
# Al importarse apunta la base de datos a una temporal. Cualquier
# modulo de backend que se cargue antes que el se queda enlazado a la
# base REAL, y entonces una prueba escribiria sobre los datos de
# verdad. Por eso no se importa dentro de las funciones.
import users_harness as h  # noqa: E402

import backend.database as database  # noqa: E402
import backend.organizations as orgs  # noqa: E402
import backend.users as users  # noqa: E402
import backend.main as servidor  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402


HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()
INICIO = io.open(RAIZ / "frontend" / "inicio.html", encoding="utf-8").read()
JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
CSS = io.open(RAIZ / "frontend" / "veyra.css", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ==============================
# EL HTML CIERRA BIEN
# ==============================

VACIOS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr", "path",
    "circle", "rect", "line", "polyline", "polygon", "use", "stop"
}


class Equilibrio(HTMLParser):
    """Lleva la cuenta de las etiquetas abiertas y sin cerrar."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pila = []
        self.errores = []

    def handle_starttag(self, tag, attrs):
        if tag not in VACIOS:
            self.pila.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):

        if tag in VACIOS:
            return

        if not self.pila:
            self.errores.append(f"</{tag}> sobra en la linea "
                                f"{self.getpos()[0]}")
            return

        if self.pila[-1][0] != tag:
            self.errores.append(
                f"linea {self.getpos()[0]}: se cierra </{tag}> pero "
                f"estaba abierto <{self.pila[-1][0]}> "
                f"(linea {self.pila[-1][1]})"
            )
            return

        self.pila.pop()


def equilibrio(texto):

    analizador = Equilibrio()
    analizador.feed(texto)

    sobran = [f"<{t}> sin cerrar (linea {n})" for t, n in analizador.pila]

    return analizador.errores + sobran


def test_las_dos_paginas_estan_bien_formadas():

    for nombre, texto in (("index.html", HTML), ("inicio.html", INICIO)):

        fallos = equilibrio(texto)

        comprobar(f"{nombre}: las etiquetas cierran",
                  not fallos, "; ".join(fallos[:3]))


# ==============================
# SE ACABARON LOS EMOJIS
# ==============================

def _simbolos(texto):
    """Caracteres que harian de icono. La puntuacion no cuenta."""

    permitidos = "‘’“”–—… ·"

    return [
        c for c in texto
        if ord(c) > 0x2000 and not c.isalpha() and c not in permitidos
    ]


def test_no_quedan_emojis_de_icono():

    for nombre, texto in (("index.html", HTML), ("inicio.html", INICIO)):

        # Solo el marcado visible, no los comentarios
        sin_comentarios = re.sub(r"<!--.*?-->", "", texto, flags=re.S)

        malos = _simbolos(sin_comentarios)

        comprobar(f"{nombre}: ningun emoji de icono",
                  not malos, repr(malos[:6]))

    # En el JavaScript se excluyen los comentarios de linea
    codigo = "\n".join(
        linea for linea in JS.split("\n")
        if not linea.strip().startswith("//")
    )

    malos = _simbolos(codigo)

    comprobar("app.js: ningun emoji de icono", not malos, repr(malos[:6]))


def test_no_hay_emojis_escapados():
    """Los que estaban como \\uXXXX contaban igual."""

    for nombre, texto in (("index.html", HTML), ("inicio.html", INICIO),
                          ("app.js", JS)):

        escapados = [
            m for m in re.findall(r"\\u([0-9a-fA-F]{4})", texto)
            if 0x2300 <= int(m, 16) <= 0x2BFF or int(m, 16) >= 0xD800
        ]

        comprobar(f"{nombre}: ningun emoji escapado",
                  not escapados, repr(escapados[:6]))


# ==============================
# LOS ICONOS EXISTEN
# ==============================

def simbolos_definidos(texto):

    return set(re.findall(r'<symbol id="(i-[a-z-]+)"', texto))


def iconos_usados(texto):

    return set(re.findall(r'href="#(i-[a-z-]+)"', texto))


def test_cada_icono_apunta_a_un_simbolo_que_existe():
    """
    Un <use> hacia un simbolo inexistente no da error: deja un hueco
    en blanco. Es el fallo mas facil de no ver.
    """

    for nombre, texto in (("index.html", HTML), ("inicio.html", INICIO)):

        definidos = simbolos_definidos(texto)
        usados = iconos_usados(texto)

        comprobar(f"{nombre}: el juego de iconos esta incluido",
                  len(definidos) > 20, f"{len(definidos)} simbolos")

        faltan = usados - definidos

        comprobar(f"{nombre}: ningun icono roto",
                  not faltan, str(sorted(faltan)))

    # Los que pinta el JavaScript
    definidos = simbolos_definidos(HTML)

    desde_js = set(
        "i-" + n for n in re.findall(r'icono\("([a-z-]+)"', JS)
    )

    faltan = desde_js - definidos

    comprobar("app.js: los iconos que pinta existen",
              not faltan, str(sorted(faltan)))


def test_el_ayudante_no_acepta_datos_de_fuera():
    """
    icono() compone marcado. Si el nombre pudiera venir del servidor,
    seria una via de inyeccion.
    """

    nombres = re.findall(r'icono\(([^)]*)\)', JS)

    sospechosos = [
        n for n in nombres
        if n.strip() and not n.strip().startswith('"')
        and not n.strip().startswith("apariencia.icono")
        and not n.strip().startswith("estado.icono")
        and "nombre" not in n and "clase" not in n
    ]

    comprobar("Los nombres de icono son literales del codigo",
              not sospechosos, str(sospechosos[:5]))


def test_los_iconos_tienen_un_solo_estilo():

    comprobar("Un unico grosor de trazo",
              CSS.count("stroke-width:") == 1
              or "stroke-width: 1.75" in CSS)

    comprobar("Heredan el color del texto",
              "stroke: currentColor" in CSS)

    # Nada de bibliotecas externas de iconos
    for biblioteca in ("lucide", "font-awesome", "fontawesome",
                       "material-icons", "bootstrap-icons", "feather"):

        comprobar(f"No se añade {biblioteca}",
                  biblioteca not in HTML.lower()
                  and biblioteca not in INICIO.lower())


# ==============================
# LOS DOS TEMAS
# ==============================

def variables_de(bloque):

    return set(re.findall(r"(--vy-[a-z-]+):", bloque))


def test_los_dos_temas_definen_lo_mismo():
    """
    Una variable que exista solo en uno de los temas deja ese color
    sin definir en el otro, y el navegador pinta negro sobre negro.
    """

    oscuro = CSS.split(':root[data-theme="dark"]', 1)[1].split("}", 1)[0]
    claro = CSS.split(':root[data-theme="light"]', 1)[1].split("}", 1)[0]

    en_oscuro = variables_de(oscuro)
    en_claro = variables_de(claro)

    comprobar("El tema oscuro define sus variables",
              len(en_oscuro) > 12, str(len(en_oscuro)))

    comprobar("El claro define exactamente las mismas",
              en_oscuro == en_claro,
              f"solo en oscuro: {sorted(en_oscuro - en_claro)}; "
              f"solo en claro: {sorted(en_claro - en_oscuro)}")


def test_el_oscuro_es_el_predeterminado():

    for nombre, texto in (("index.html", HTML), ("inicio.html", INICIO)):

        bloque = texto.split("data-theme", 1)[0]

        comprobar(f"{nombre}: el tema se aplica antes de pintar",
                  'var tema = "dark"' in texto)

        comprobar(f"{nombre}: el predeterminado es el oscuro",
                  'setAttribute("data-theme", tema)' in texto)


def test_el_claro_no_es_el_oscuro_invertido():

    claro = CSS.split(':root[data-theme="light"]', 1)[1].split("}", 1)[0]

    comprobar("El modo claro tiene fondo claro",
              "--vy-fondo: #F4F6FB" in claro, claro[:120])

    comprobar("Y tarjetas blancas",
              "--vy-superficie: #FFFFFF" in claro)

    comprobar("Con texto oscuro",
              "--vy-texto: #0F1B2E" in claro)

    # El azul del claro es mas profundo, para contrastar sobre blanco
    comprobar("Y un azul propio, no el mismo del oscuro",
              "--vy-acento: #1D63E6" in claro)


def test_el_tema_cubre_lo_que_ya_estaba_escrito():
    """
    El panel tiene cientos de clases de color de Tailwind escritas a
    mano. Si no se reasignan, en modo oscuro quedan trozos blancos.
    """

    for utilidad in ("bg-white", "bg-slate-50", "bg-slate-100",
                     "text-slate-900", "text-slate-500",
                     "border-slate-200"):

        comprobar(f"{utilidad} obedece al tema",
                  utilidad in CSS, "no se reasigna")

    comprobar("Los campos de formulario tambien",
              "input," in CSS and "--vy-superficie-suave" in CSS)

    comprobar("Y el autorrelleno del navegador",
              "-webkit-autofill" in CSS)


def test_la_preferencia_es_de_cada_usuario():

    comprobar("Hay una clave por usuario",
              "claveDelTema" in JS)

    bloque = JS.split("function claveDelTema", 1)[1].split("\n}", 1)[0]

    comprobar("Que incluye el nombre del usuario",
              "usuario ?" in bloque and "+ usuario" in bloque, bloque)

    comprobar("Al entrar se adopta la del usuario",
              "adoptarTemaDeUsuario(username)" in JS)

    comprobar("Al salir se suelta el vinculo",
              "olvidarTemaDeUsuario()" in JS)

    # Sin preferencia propia NO se hereda la del anterior
    adoptar = JS.split("function adoptarTemaDeUsuario", 1)[1] \
        .split("\n}", 1)[0]

    comprobar("Quien entra sin preferencia estrena el oscuro",
              "aplicarTema(TEMA_OSCURO, true)" in adoptar, adoptar[-200:])


def test_el_almacenamiento_nunca_tumba_la_pagina():
    """
    localStorage lanza en navegacion privada. El panel tiene que
    seguir funcionando.
    """

    for funcion in ("leerAlmacen", "guardarAlmacen"):

        bloque = JS.split(f"function {funcion}", 1)[1].split("\n}", 1)[0]

        comprobar(f"{funcion} captura el error",
                  "catch" in bloque, bloque[:100])

    for nombre, texto in (("index.html", HTML), ("inicio.html", INICIO)):

        cabecera = texto.split("</head>", 1)[0]

        comprobar(f"{nombre}: el script de arranque tambien",
                  "catch (error)" in cabecera)


def test_no_se_guarda_nada_de_la_sesion():
    """El token sigue viviendo solo en la cookie HttpOnly."""

    # Todo el guardado pasa por guardarAlmacen(); se comprueba con que
    # claves se la llama.
    # Solo las LLAMADAS, no la definicion de la propia funcion
    cuerpo = JS.split("function guardarAlmacen", 1)
    sin_definicion = cuerpo[0] + cuerpo[1].split(chr(10) + "}", 1)[1]

    claves = re.findall(r'guardarAlmacen\(\s*([^,]+),', sin_definicion)

    comprobar("Se guarda algo", claves, str(claves))

    comprobar("Y solo son claves de tema",
              all("TEMA" in c or "Tema" in c for c in claves),
              str(claves))

    directos = re.findall(r'localStorage\.setItem\(\s*"([^"]+)"', JS)

    comprobar("Sin guardados sueltos fuera del ayudante",
              not directos, str(directos))

    for prohibido in ("token", "password", "contrasena", "cookie",
                      "session"):

        comprobar(f"Nada llamado '{prohibido}' se guarda",
                  f'setItem("{prohibido}' not in JS.lower())


# ==============================
# NAVEGACION ADAPTABLE
# ==============================

def test_la_barra_ya_no_desaparece_sin_alternativa():
    """
    El fallo original: class="hidden md:flex". Por debajo de 768px la
    barra se ocultaba y no quedaba ninguna forma de navegar.
    """

    aside = HTML.split("<aside", 1)[1].split(">", 1)[0]

    comprobar("La barra no se oculta sin mas",
              "hidden" not in aside, aside)

    comprobar("Hay un boton de menu",
              'id="vy-menu-boton"' in HTML)

    comprobar("Y un boton para contraer o ampliar",
              'id="vy-ampliar"' in HTML)


def test_el_cajon_se_abre_y_se_cierra_de_tres_formas():

    comprobar("Se abre", "function abrirCajon" in JS)
    comprobar("Se cierra", "function cerrarCajon" in JS)

    conectar = JS.split("function conectarInterfazVeyra", 1)[1] \
        .split("\n}\n", 1)[0]

    comprobar("Con el boton de cerrar",
              'getElementById("vy-cerrar-cajon")' in conectar)

    comprobar("Tocando el fondo",
              'getElementById("vy-capa")' in conectar
              and "cerrarCajon" in conectar)

    comprobar("Y con Escape",
              '"Escape"' in conectar)

    comprobar("Al elegir destino se cierra solo",
              "cerrarCajon();" in JS.split("querySelectorAll(\".nav-button\")",
                                           1)[1][:900])


def test_hay_un_comportamiento_para_cada_ancho():

    for corte in ("1023px", "767px"):

        comprobar(f"Hay un punto de corte en {corte}",
                  f"max-width: {corte}" in CSS)

    comprobar("En movil la barra sale de la vista",
              "translateX(-100%)" in CSS)

    comprobar("Y vuelve al abrirse",
              ".vy-sidebar.is-abierta" in CSS)

    comprobar("En tableta se queda compacta",
              "is-compacta" in CSS or "is-expandida" in CSS)

    comprobar("El codigo y el CSS usan el mismo umbral",
              "ANCHO_MOVIL = 767" in JS)


def test_las_tablas_no_arrastran_la_pagina():

    comprobar("Las tablas se desplazan por dentro",
              ".vy-tabla-scroll" in CSS and "overflow-x: auto" in CSS)

    comprobar("Y la pagina nunca en horizontal",
              "overflow-x: hidden" in CSS)


def test_los_controles_se_pueden_tocar():

    comprobar("Los botones tienen altura de dedo",
              "min-height: 40px" in CSS)


def test_el_foco_se_ve_en_los_dos_temas():

    comprobar("Hay anillo de foco",
              ":focus-visible" in CSS and "outline:" in CSS)

    comprobar("El color del anillo depende del tema",
              "--vy-anillo" in CSS)

    oscuro = CSS.split(':root[data-theme="dark"]', 1)[1].split("}", 1)[0]
    claro = CSS.split(':root[data-theme="light"]', 1)[1].split("}", 1)[0]

    comprobar("Definido en oscuro", "--vy-anillo" in oscuro)
    comprobar("Y en claro", "--vy-anillo" in claro)


def test_se_respeta_quien_pide_menos_movimiento():

    comprobar("prefers-reduced-motion se atiende",
              "prefers-reduced-motion" in CSS)


# ==============================
# LA PAGINA PUBLICA
# ==============================

def test_el_encabezado_lleva_lo_pedido():

    for texto in ("Inicio", "Funciones", "Seguridad", "Planes",
                  "Contacto", "Iniciar sesi"):

        comprobar(f"El encabezado tiene '{texto}'", texto in INICIO)

    comprobar("Y la marca completa",
              "VE" in INICIO and "by NYRA" in INICIO)

    comprobar("Con selector de tema",
              'id="tema-boton"' in INICIO)


def test_ningun_enlace_es_decorativo():
    """Cada enlace lleva a una seccion de la pagina o a una ruta real."""

    RUTAS_REALES = {"/panel", "/inicio", "/", "/frontend/index.html"}

    enlaces = re.findall(r'<a[^>]*href="([^"]+)"', INICIO)

    comprobar("Hay enlaces", len(enlaces) > 8, str(len(enlaces)))

    rotos = []

    for destino in enlaces:

        if destino.startswith("#"):

            # El ancla tiene que existir en la pagina
            if f'id="{destino[1:]}"' not in INICIO:
                rotos.append(destino)

        elif destino.startswith("mailto:"):
            continue

        elif destino not in RUTAS_REALES:
            rotos.append(destino)

    comprobar("Ninguno lleva a la nada", not rotos, str(rotos))


def test_el_mensaje_principal_es_el_pedido():

    comprobar("Esta el titular",
              "Administra tus equipos" in INICIO
              and "desde un solo lugar" in INICIO)

    comprobar("Con las dos llamadas a la accion",
              "Solicitar acceso" in INICIO and "Iniciar sesi" in INICIO)


def test_las_cinco_funciones_existen_en_el_producto():

    for titulo in ("Monitoreo de equipos", "Grabaciones de pantalla",
                   "Gesti", "Organizaciones separadas",
                   "Seguridad y control"):

        comprobar(f"Se presenta '{titulo}'", titulo in INICIO)


def test_las_tres_tarjetas_llevan_a_algo():

    for titulo in ("Qu", "Cómo empezar", "Necesitas ayuda"):
        comprobar(f"Tarjeta '{titulo}'", titulo in INICIO)


# ==============================
# NADA INVENTADO
# ==============================

def test_no_se_inventan_precios():

    import re as _re

    for senal in ("$", "€", "/mes", "al mes", "USD", "EUR", "MXN"):

        comprobar(f"Sin '{senal}' en la pagina publica",
                  senal not in INICIO)

    # Decir "el precio se acuerda al contratar" es lo contrario a
    # inventarse uno. Lo que no puede aparecer es una CIFRA de dinero.
    texto = _re.sub(r"<[^>]+>", " ", INICIO)

    cifras = _re.findall(r"\d+[.,]\d{2}\s*(?:€|\$|USD|EUR)", texto)

    comprobar("Ninguna cantidad de dinero", not cifras, str(cifras))


def test_no_se_inventan_testimonios_ni_clientes():

    for senal in ("testimonio", "Testimonio", "clientes conf",
                  "empresas conf", "valoración", "4.8", "5 estrellas"):

        comprobar(f"Sin '{senal}'", senal not in INICIO)


def test_no_se_inventa_un_correo_de_contacto():
    """
    Un correo inventado es peor que ninguno: quien escriba no recibe
    respuesta.
    """

    correos = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", INICIO)

    comprobar("No hay ninguna direccion inventada",
              not correos, str(correos))

    comprobar("Y el hueco esta preparado para la real",
              "CORREO_DE_CONTACTO" in INICIO)

    comprobar("Mientras no la haya, no se pinta enlace",
              "if (!CORREO_DE_CONTACTO)" in INICIO)


def test_los_planes_son_los_del_backend():
    """Los limites de la pagina tienen que ser los que aplica el servidor."""

    for clave, datos in orgs.PLANS.items():

        comprobar(f"El plan {datos['label']} aparece",
                  datos["label"] in INICIO)

        if datos["max_devices"]:
            comprobar(f"{datos['label']}: {datos['max_devices']} equipos",
                      str(datos["max_devices"]) in INICIO)

    comprobar("Sin limite se dice con palabras",
              "sin límite" in INICIO.lower())


def test_la_maqueta_se_declara_como_ejemplo():
    """
    La vista previa es una ilustracion. No puede confundirse con
    datos reales de una empresa.
    """

    comprobar("La maqueta se oculta a los lectores de pantalla",
              'class="vy-maqueta" aria-hidden="true"' in INICIO)

    comprobar("Y lo dice tambien por escrito",
              "Vista de ejemplo" in INICIO)

    comprobar("Aclarando que no es de ninguna organizacion",
              "no corresponden a ninguna organizaci" in INICIO)

    # Nada de pasar una captura real por maqueta, ni al reves
    comprobar("No hay capturas de pantalla en la portada",
              "veyra-panel-" not in INICIO)


def test_la_maqueta_refleja_el_panel_de_verdad():
    """
    Antes copiaba la ilustracion de referencia, con destinos que el
    producto no tiene. Si la maqueta enseña un menu que luego no
    existe, promete algo que no hay.
    """

    maqueta = INICIO.split('class="vy-maqueta"', 1)[1].split(
        "Vista de ejemplo", 1
    )[0]

    # Los seis destinos reales, con el nombre que usa el panel
    for destino in ("Dashboard", "Dispositivos", "Grabaciones",
                    "Alertas", "Instalar equipo", "Configuración"):

        comprobar(f"La maqueta enseña '{destino}'",
                  destino in maqueta)

    comprobar("Agrupados como en la barra lateral",
              "Principal" in maqueta and "Sistema" in maqueta)

    comprobar("Con la insignia de alertas",
              "vy-maqueta-insignia" in maqueta)

    comprobar("Y la cabecera con el titulo de la vista",
              "Resumen de tus dispositivos" in maqueta)

    # Las cinco tarjetas del Resumen, con sus nombres reales
    for etiqueta in ("Equipos", "En línea", "Sin conexión",
                     "Alertas", "En riesgo"):

        comprobar(f"Tarjeta '{etiqueta}'", etiqueta in maqueta)

    comprobar("Y los dos paneles de abajo",
              "Estado general" in maqueta
              and "Salud del sistema" in maqueta)


def test_la_maqueta_no_inventa_destinos():
    """
    Lo contrario del anterior: que no aparezca nada que el panel no
    tenga.
    """

    maqueta = INICIO.split('class="vy-maqueta"', 1)[1].split(
        "Vista de ejemplo", 1
    )[0]

    vistas = set(
        __import__("re").findall(r'data-view="([a-z]+)"', HTML)
    )

    comprobar("El panel tiene seis vistas", len(vistas) == 6, str(vistas))

    # Destinos que la referencia mostraba pero el producto no tiene
    # como seccion propia del menu
    for inventado in ("Organización</span>", "Usuarios</span>"):

        comprobar(f"La maqueta no promete '{inventado[:12]}'",
                  inventado not in maqueta)


def test_la_maqueta_usa_las_piezas_del_panel():
    """
    Esta hecha con las mismas variables y los mismos iconos que el
    panel. Asi se parece sola, y seguira parecidose cuando el tema
    cambie.
    """

    maqueta = INICIO.split('class="vy-maqueta"', 1)[1].split(
        "Vista de ejemplo", 1
    )[0]

    comprobar("Usa las variables del tema",
              "var(--vy-" in maqueta)

    comprobar("Y los iconos del juego",
              'href="#i-' in maqueta)

    comprobar("Con el logotipo de la barra",
              "veyra-logo-claro" in maqueta)

    # Las clases de la maqueta existen en el CSS
    import re as _re

    clases = set(_re.findall(r'class="(vy-maqueta[a-z-]*)"', maqueta))

    for clase in clases:
        comprobar(f"La clase .{clase} esta definida",
                  "." + clase in CSS)

    comprobar("Y responde a los dos temas",
              ':root[data-theme="light"] .vy-maqueta-area' in CSS)


def test_no_se_promete_un_registro_que_no_existe():
    """
    No hay alta publica: las organizaciones las crea el operador. La
    pagina no puede prometer una cuenta inmediata.
    """

    fuente = io.open(RAIZ / "backend" / "main.py",
                     encoding="utf-8").read()

    bloque = fuente.split('@app.post("/api/organizations")', 1)[1][:400]

    comprobar("Crear organizacion sigue siendo solo del operador",
              "require_platform_owner" in bloque)

    comprobar("La pagina explica el proceso real",
              "no tiene registro abierto" in INICIO)

    comprobar("Y no anuncia una cuenta instantanea",
              "Crear cuenta gratis" not in INICIO
              and "Regístrate gratis" not in INICIO)


# ==============================
# LA MARCA
# ==============================

def test_el_favicon_es_la_marca():

    for nombre, texto in (("index.html", HTML), ("inicio.html", INICIO)):

        comprobar(f"{nombre}: el favicon es un archivo, no un dibujo "
                  "improvisado",
                  'href="/frontend/marca/veyra.ico"' in texto)

        comprobar(f"{nombre}: con varios tamaños disponibles",
                  "veyra-32.png" in texto and "veyra-48.png" in texto)

        comprobar(f"{nombre}: y el de iOS",
                  'rel="apple-touch-icon"' in texto)

        comprobar(f"{nombre}: ya no queda el favicon provisional",
                  "data:image/svg+xml" not in texto)


def test_los_archivos_de_marca_existen_y_pesan_poco():

    carpeta = RAIZ / "frontend" / "marca"

    ESPERADOS = {
        "veyra.ico": 64,
        "veyra-32.png": 8,
        "veyra-48.png": 12,
        "veyra-180.png": 64,
        "veyra-256.png": 96,
        "veyra-logo-320.png": 64,
        "veyra-logo-claro-320.png": 64,
        "veyra-logo-640.png": 160,
        "veyra-logo-claro-640.png": 160,
    }

    for nombre, tope_kb in ESPERADOS.items():

        ruta = carpeta / nombre

        comprobar(f"Existe {nombre}", ruta.exists())

        if not ruta.exists():
            continue

        kb = ruta.stat().st_size / 1024

        # El original pesaba 1 MB. Servirlo tal cual en cada carga
        # seria absurdo para algo que se pinta a 16 pixeles.
        comprobar(f"{nombre} pesa poco ({kb:.0f} KB)",
                  kb < tope_kb, f"{kb:.0f} KB, tope {tope_kb}")


def test_el_logotipo_se_lee_en_los_dos_temas():
    """
    La palabra del logotipo es azul marino casi negro: sobre fondo
    oscuro desaparece. Por eso hay dos variantes y se alterna.
    """

    comprobar("Hay una variante para fondos oscuros",
              (RAIZ / "frontend" / "marca"
               / "veyra-logo-claro-320.png").exists())

    comprobar("CSS alterna segun el tema",
              ".vy-logo-sobre-oscuro" in CSS
              and ".vy-logo-sobre-claro" in CSS)

    oscuro = CSS.split(
        ':root[data-theme="dark"] .vy-logo-sobre-oscuro', 1
    )[1].split("}", 1)[0]

    comprobar("En tema oscuro se enseña la variante clara",
              "display: block" in oscuro, oscuro)

    # Donde el fondo depende del tema, estan las dos
    for nombre, texto in (("index.html", HTML), ("inicio.html", INICIO)):

        comprobar(f"{nombre}: estan las dos variantes",
                  "veyra-logo-320.png" in texto
                  and "veyra-logo-claro-320.png" in texto)


def test_la_barra_lateral_usa_la_variante_clara():
    """
    La barra es azul marino en LOS DOS temas, asi que ahi no se
    alterna: siempre la clara. Si se colara la oscura, el nombre del
    producto quedaria invisible.
    """

    barra = HTML.split('id="vy-sidebar"', 1)[1].split("</aside>", 1)[0]
    cabecera = barra.split("vy-sidebar-cabecera", 1)[1].split(
        "vy-sidebar-nav", 1
    )[0]

    comprobar("La barra lleva la variante clara",
              "veyra-logo-claro-320.png" in cabecera)

    comprobar("Y NO la oscura, que no se veria",
              "veyra-logo-320.png" not in cabecera.replace(
                  "veyra-logo-claro-320.png", ""
              ), cabecera[:200])


def test_el_nombre_no_se_dice_dos_veces():
    """
    El logotipo ya contiene la palabra VEYRA. Dejar ademas el bloque
    de texto al lado la repetiria.
    """

    barra = HTML.split('id="vy-sidebar"', 1)[1].split("</aside>", 1)[0]
    cabecera = barra.split("vy-sidebar-cabecera", 1)[1].split(
        "vy-sidebar-nav", 1
    )[0]

    comprobar("Sin la palabra repetida en texto",
              "vy-marca-nombre" not in cabecera, cabecera[:200])


def test_en_compacta_queda_el_dibujo():
    """A 72px no cabe la palabra; el dibujo sigue identificando."""

    comprobar("Hay una version de solo dibujo",
              ".vy-logo-icono" in CSS)

    comprobar("Que aparece al contraer la barra",
              ".vy-sidebar.is-compacta .vy-logo-icono" in CSS)

    comprobar("Y entonces se retira la ancha",
              ".vy-sidebar.is-compacta .vy-logo-ancho" in CSS)

    barra = HTML.split('id="vy-sidebar"', 1)[1].split("</aside>", 1)[0]

    comprobar("Las dos estan en el marcado",
              "vy-logo-ancho" in barra and "vy-logo-icono" in barra)


def test_el_logotipo_no_se_anuncia_dos_veces():
    """
    Con dos imagenes del mismo logotipo, un lector de pantalla diria
    "VEYRA" dos veces si ambas tuvieran texto alternativo.
    """

    import re as _re

    for nombre, texto in (("index.html", HTML), ("inicio.html", INICIO)):

        ocultas = _re.findall(
            r'<img[^>]*vy-logo-sobre-oscuro[^>]*>', texto, _re.S
        )

        for etiqueta in ocultas:

            comprobar(f"{nombre}: la copia oculta no se anuncia",
                      'aria-hidden="true"' in etiqueta
                      and 'alt=""' in etiqueta,
                      etiqueta[:120])


def test_las_imagenes_reservan_su_sitio():
    """
    Sin width y height, la pagina salta cuando carga la imagen.
    """

    import re as _re

    for nombre, texto in (("index.html", HTML), ("inicio.html", INICIO)):

        # Solo las de marca. El visor de escritorio remoto se queda
        # fuera a proposito: sus medidas son las del equipo al otro
        # lado y no se pueden saber de antemano.
        imagenes = [
            i for i in _re.findall(r'<img[^>]*>', texto, _re.S)
            if "/frontend/marca/" in i
        ]

        comprobar(f"{nombre}: hay imagenes de marca", imagenes, "ninguna")

        sin_medidas = [
            i for i in imagenes
            if "width=" not in i or "height=" not in i
        ]

        comprobar(f"{nombre}: todas reservan su sitio",
                  not sin_medidas, str(sin_medidas[:1]))


# ==============================
# NO SE ROMPIO NADA
# ==============================

def test_siguen_estando_los_identificadores_del_panel():

    for ident in ("login-screen", "app-shell", "login-form",
                  "login-username", "login-password", "login-button",
                  "login-error", "forgot-link", "forgot-panel",
                  "session-username", "logout-button",
                  "view-title", "view-subtitle", "server-status",
                  "alerts-badge", "stats-section", "devices-section",
                  "total-devices", "online-devices", "offline-devices",
                  "pending-alerts", "warning-devices", "nodata-devices"):

        comprobar(f"Sigue existiendo #{ident}",
                  f'id="{ident}"' in HTML)


def test_siguen_estando_las_seis_vistas():

    for vista in ("dashboard", "devices", "recordings", "alerts",
                  "install", "settings"):

        comprobar(f"El boton de {vista} sigue",
                  f'id="nav-{vista}"' in HTML
                  and f'data-view="{vista}"' in HTML)

        comprobar(f"Y showView() sigue atendiendo {vista}",
                  f'view === "{vista}"' in JS)


def test_el_panel_sigue_sirviendose():

    fuente = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()

    comprobar("La raiz sirve la pagina publica",
              'return FileResponse(\n        "frontend/inicio.html"'
              in fuente)

    comprobar("El panel responde en /panel",
              '@app.get("/panel")' in fuente)

    comprobar("Y sigue estando en /frontend/index.html",
              'app.mount(\n    "/frontend"' in fuente)


def test_el_enlace_de_recuperacion_no_se_movio():
    """
    El correo de recuperacion apunta a /frontend/index.html. Si la
    raiz cambia de contenido, ese enlace tiene que seguir valiendo.
    """

    mailer = io.open(RAIZ / "backend" / "mailer.py", encoding="utf-8").read()

    comprobar("El enlace sigue apuntando al panel",
              "/frontend/index.html#reset=" in mailer)


def test_no_se_añadieron_dependencias():

    for pagina in (HTML, INICIO):

        externos = re.findall(r'src="(https?://[^"]+)"', pagina)
        hojas = re.findall(r'<link[^>]*href="(https?://[^"]+)"', pagina)

        # Tailwind por CDN ya estaba antes del rediseno
        nuevos = [
            u for u in externos + hojas
            if "cdn.tailwindcss.com" not in u
        ]

        comprobar("Ninguna dependencia externa nueva",
                  not nuevos, str(nuevos))


def test_los_datos_del_servidor_no_se_inyectan_como_html():
    """
    Se metio marcado nuevo en plantillas de cadena. Lo que viene del
    backend tiene que seguir yendo por textContent.
    """

    rotulo = JS.split('getElementById("vy-usuario-org")', 1)[1][:400]

    comprobar("La organizacion se pinta con textContent",
              "textContent" in rotulo and "innerHTML" not in rotulo)


# ==============================
# SE SIRVE DE VERDAD
# ==============================

def test_las_paginas_responden():

    cliente = h.cliente(h.OWNER)

    for ruta, marca in (("/", "Administra tus equipos"),
                        ("/inicio", "Administra tus equipos"),
                        ("/panel", 'id="app-shell"'),
                        ("/frontend/index.html", 'id="app-shell"'),
                        ("/frontend/veyra.css", "--vy-acento"),
                        ("/frontend/inicio.html", "Solicitar acceso")):

        respuesta = cliente.get(ruta)

        comprobar(f"{ruta} responde 200",
                  respuesta.status_code == 200,
                  str(respuesta.status_code))

        comprobar(f"{ruta} trae lo que debe",
                  marca in respuesta.text, marca)


def test_la_pagina_publica_no_filtra_datos():
    """
    Es publica: no puede traer nada de ninguna organizacion real.
    """

    h.reiniciar()

    # Las organizaciones NO se borran: users.organization_id va en
    # cascada, asi que vaciarlas se lleva por delante al owner que
    # reiniciar() acaba de sembrar.
    conexion = database.get_connection()
    for tabla in ("recordings", "devices"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    org = orgs.create_organization(
        "Plastika Industrial", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, org["id"])

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES ('eq-real', 'PC-CONTABILIDAD', ?)", (org["id"],)
    )
    conexion.commit()
    conexion.close()

    # Sin sesion, como un visitante cualquiera
    visitante = TestClient(servidor.app)

    respuesta = visitante.get("/")

    comprobar("Un visitante ve la pagina publica",
              respuesta.status_code == 200)

    for secreto in ("Plastika", "PC-CONTABILIDAD", "eq-real",
                    h.OWNER):

        comprobar(f"Y no aparece '{secreto}'",
                  secreto not in respuesta.text)


# ==============================

def main():

    pruebas = [
        test_las_dos_paginas_estan_bien_formadas,
        test_no_quedan_emojis_de_icono,
        test_no_hay_emojis_escapados,
        test_cada_icono_apunta_a_un_simbolo_que_existe,
        test_el_ayudante_no_acepta_datos_de_fuera,
        test_los_iconos_tienen_un_solo_estilo,
        test_los_dos_temas_definen_lo_mismo,
        test_el_oscuro_es_el_predeterminado,
        test_el_claro_no_es_el_oscuro_invertido,
        test_el_tema_cubre_lo_que_ya_estaba_escrito,
        test_la_preferencia_es_de_cada_usuario,
        test_el_almacenamiento_nunca_tumba_la_pagina,
        test_no_se_guarda_nada_de_la_sesion,
        test_la_barra_ya_no_desaparece_sin_alternativa,
        test_el_cajon_se_abre_y_se_cierra_de_tres_formas,
        test_hay_un_comportamiento_para_cada_ancho,
        test_las_tablas_no_arrastran_la_pagina,
        test_los_controles_se_pueden_tocar,
        test_el_foco_se_ve_en_los_dos_temas,
        test_se_respeta_quien_pide_menos_movimiento,
        test_el_encabezado_lleva_lo_pedido,
        test_ningun_enlace_es_decorativo,
        test_el_mensaje_principal_es_el_pedido,
        test_las_cinco_funciones_existen_en_el_producto,
        test_las_tres_tarjetas_llevan_a_algo,
        test_no_se_inventan_precios,
        test_no_se_inventan_testimonios_ni_clientes,
        test_no_se_inventa_un_correo_de_contacto,
        test_los_planes_son_los_del_backend,
        test_la_maqueta_se_declara_como_ejemplo,
        test_la_maqueta_refleja_el_panel_de_verdad,
        test_la_maqueta_no_inventa_destinos,
        test_la_maqueta_usa_las_piezas_del_panel,
        test_no_se_promete_un_registro_que_no_existe,
        test_el_favicon_es_la_marca,
        test_los_archivos_de_marca_existen_y_pesan_poco,
        test_el_logotipo_se_lee_en_los_dos_temas,
        test_la_barra_lateral_usa_la_variante_clara,
        test_el_nombre_no_se_dice_dos_veces,
        test_en_compacta_queda_el_dibujo,
        test_el_logotipo_no_se_anuncia_dos_veces,
        test_las_imagenes_reservan_su_sitio,
        test_siguen_estando_los_identificadores_del_panel,
        test_siguen_estando_las_seis_vistas,
        test_el_panel_sigue_sirviendose,
        test_el_enlace_de_recuperacion_no_se_movio,
        test_no_se_añadieron_dependencias,
        test_los_datos_del_servidor_no_se_inyectan_como_html,
        test_las_paginas_responden,
        test_la_pagina_publica_no_filtra_datos
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:110])

    fallos = 0

    for nombre, ok, detalle in resultados:
        marca = "OK  " if ok else "FALLO"
        print(f"[{marca}] {nombre}" + (f"  -> {detalle}" if not ok else ""))
        if not ok:
            fallos += 1

    print(f"\n{len(resultados) - fallos}/{len(resultados)} comprobaciones "
          "correctas")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
