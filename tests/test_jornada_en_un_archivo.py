r"""
De cada dia y equipo queda UN video, no treinta y seis trozos.

    .venv\Scripts\python tests/test_jornada_en_un_archivo.py

Lo que se pidio, literal: "solo debemos guardar un video por dia y
equipo". Hasta ahora el sistema guardaba los segmentos de quince
minutos y el panel los reproducia encadenados, que es un apano de
visualizacion: para descargar, archivar o entregar una jornada
seguian siendo noventa y seis archivos.

Se sigue CAPTURANDO en trozos, y eso no cambia: un MP4 no se puede
reproducir hasta que se cierra, asi que una jornada en un solo
archivo abierto desde las 08:00 se pierde entera si alguien apaga el
kiosco a las 16:00. Lo que cambia es que al terminar el dia los
trozos se unen en uno y se retiran.

Las pruebas van con VIDEOS DE VERDAD, hechos con el mismo ffmpeg que
lleva el Agent. Unir archivos es justo el terreno donde una prueba con
datos falsos no demuestra nada: lo que falla son los parametros del
flujo, las rutas con barra invertida y las duraciones, y nada de eso
aparece si los "videos" son archivos de texto.

Lo que se comprueba, y por que cada cosa:

  une y cuadra          la jornada dura lo que suman sus trozos

  borra DESPUES         y solo si cuadra: unir mal y borrar es perder
                        el dia; unir mal y no borrar es un dia en
                        pedazos, que se sigue pudiendo ver

  resolucion distinta   '-c copy' exige los mismos parametros. Si a
                        media jornada cambio la pantalla, ffmpeg
                        produce un archivo truncado SIN dar error.
                        Ese es el caso que de verdad puede costar un
                        dia de grabacion, y aqui se provoca a
                        proposito

  retoma un borrado     si se corto entre unir y borrar, la segunda
  a medias              pasada verifica y limpia, no vuelve a unir

  no se come a si misma el archivo del dia no se llama rec_*, asi que
                        una segunda pasada no lo mete dentro de si
"""

import io
import os
import subprocess
import sys
import time
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

import imageio_ffmpeg  # noqa: E402

# 'agent' a secas resuelve a agent/agent.py, que arrastra wmi y
# pyautogui. Los modulos del Agent se importan planos entre si,
# igual que hace recorder.py con paths.
import jornada  # noqa: E402


FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


def _sin_ventana():
    if os.name != "nt":
        return {}
    return {"creationflags": 0x08000000}


def video_de_prueba(ruta, segundos, ancho=160, alto=120):
    """
    Un video H.264 de verdad, con los mismos parametros que el
    grabador: libx264 y yuv420p.
    """

    subprocess.run(
        [
            FFMPEG, "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "testsrc=size=%dx%d:rate=10:duration=%s"
                  % (ancho, alto, segundos),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            ruta
        ],
        capture_output=True,
        timeout=120,
        **_sin_ventana()
    )

    return ruta


def dia_de_prueba(duraciones, ancho=160, alto=120):
    """Una carpeta de dia con sus segmentos, como la deja el grabador."""

    carpeta = tempfile.mkdtemp(prefix="jornada-")

    # Marcas de tiempo crecientes: el orden de la jornada es el del
    # nombre, igual que en el equipo.
    for i, segundos in enumerate(duraciones):
        video_de_prueba(
            os.path.join(carpeta, "rec_%d.mp4" % (1790000000 + i * 900)),
            segundos, ancho, alto
        )

    return carpeta


def prueba_une_y_cuadra():

    carpeta = dia_de_prueba([2, 2, 2])

    segmentos = jornada.segmentos_del_dia(carpeta)

    comprobar("encuentra los tres segmentos", len(segmentos) == 3,
              segmentos)

    comprobar("y en orden",
              segmentos == sorted(segmentos))

    resultado = jornada.cerrar_la_jornada(carpeta, "2026-11-09")

    comprobar("la jornada se cierra", resultado.get("unida"),
              resultado.get("motivo"))

    destino = jornada.ruta_de_la_jornada(carpeta, "2026-11-09")

    comprobar("existe el video del dia", os.path.isfile(destino))

    medida = jornada.duracion(destino)

    comprobar("y dura la suma de los trozos (~6 s)",
              medida is not None and 5.0 <= medida <= 7.5,
              medida)

    comprobar("los trozos ya no estan",
              jornada.segmentos_del_dia(carpeta) == [])

    comprobar("queda UN solo archivo en la carpeta",
              len(os.listdir(carpeta)) == 1,
              os.listdir(carpeta))


def prueba_no_borra_si_no_cuadra():
    """
    El caso que de verdad puede costar un dia.

    Dos segmentos con resoluciones distintas. '-c copy' no puede
    unirlos bien: produce un archivo reproducible pero truncado y
    devuelve 0, sin error. Si se borrara por fe, el dia se perderia.
    """

    carpeta = tempfile.mkdtemp(prefix="jornada-rota-")

    video_de_prueba(os.path.join(carpeta, "rec_1790000000.mp4"), 2,
                    ancho=160, alto=120)
    video_de_prueba(os.path.join(carpeta, "rec_1790000900.mp4"), 2,
                    ancho=320, alto=240)

    antes = jornada.segmentos_del_dia(carpeta)

    resultado = jornada.cerrar_la_jornada(carpeta, "2026-11-09")

    # Puede fallar al unir o al verificar: las dos salidas son
    # correctas. Lo que NO puede pasar es que borre.
    comprobar("con resoluciones distintas NO se da por cerrada",
              not resultado.get("unida"),
              resultado)

    comprobar("y los segmentos siguen todos ahi",
              jornada.segmentos_del_dia(carpeta) == antes,
              jornada.segmentos_del_dia(carpeta))


def prueba_cuadra_detecta_el_truncado():
    """
    La comprobacion de duracion, aislada.

    Se le da un archivo que no contiene lo que suman los trozos y
    tiene que decir que no cuadra.
    """

    carpeta = dia_de_prueba([2, 2, 2])
    segmentos = jornada.segmentos_del_dia(carpeta)

    # Un "dia" que en realidad es solo el primer trozo
    falso = os.path.join(carpeta, "jornada_falsa.mp4")
    video_de_prueba(falso, 2)

    bien, motivo = jornada.cuadra(segmentos, falso)

    comprobar("detecta que el dia dura menos que sus trozos",
              not bien, motivo)

    bueno = os.path.join(carpeta, "jornada_buena.mp4")
    ok, _ = jornada.unir(segmentos, bueno)

    if ok:
        bien, motivo = jornada.cuadra(segmentos, bueno)
        comprobar("y acepta la union correcta", bien, motivo)
    else:
        comprobar("y acepta la union correcta", False, "no se pudo unir")


def prueba_retoma_un_borrado_a_medias():
    """
    Si se corto entre unir y borrar, la segunda pasada limpia.

    No vuelve a unir: el archivo del dia ya esta bien, y rehacer nueve
    horas por un trozo que sobro seria trabajo tirado.
    """

    carpeta = dia_de_prueba([2, 2])
    segmentos = jornada.segmentos_del_dia(carpeta)

    destino = jornada.ruta_de_la_jornada(carpeta, "2026-11-09")

    ok, motivo = jornada.unir(segmentos, destino)

    comprobar("se puede unir a mano", ok, motivo)

    # Aqui "se corta la luz": el archivo del dia existe y los trozos
    # tambien.
    comprobar("el dia ya estaba unido",
              jornada.ya_esta_unida(carpeta, "2026-11-09"))

    resultado = jornada.cerrar_la_jornada(carpeta, "2026-11-09")

    comprobar("la segunda pasada la da por cerrada",
              resultado.get("unida"), resultado.get("motivo"))

    comprobar("y se nota que fue un reintento",
              resultado.get("reintento") is True)

    comprobar("los trozos se retiraron",
              jornada.segmentos_del_dia(carpeta) == [])


def prueba_el_dia_no_se_come_a_si_mismo():
    """
    El archivo del dia no se llama rec_*, asi que no es un segmento.

    Sin esto, una segunda pasada lo uniria DENTRO de si mismo y el
    dia crearia una copia de su propia copia en cada vuelta.
    """

    carpeta = dia_de_prueba([2])

    destino = jornada.ruta_de_la_jornada(carpeta, "2026-11-09")

    jornada.cerrar_la_jornada(carpeta, "2026-11-09")

    comprobar("el video del dia no cuenta como segmento",
              destino not in jornada.segmentos_del_dia(carpeta))

    comprobar("una segunda pasada no encuentra nada que unir",
              jornada.cerrar_la_jornada(
                  carpeta, "2026-11-09").get("motivo") == "sin segmentos")


def prueba_una_carpeta_vacia_no_revienta():

    carpeta = tempfile.mkdtemp(prefix="jornada-vacia-")

    resultado = jornada.cerrar_la_jornada(carpeta, "2026-11-09")

    comprobar("sin segmentos no se cierra nada",
              not resultado.get("unida"))

    comprobar("y no se crea ningun archivo",
              os.listdir(carpeta) == [],
              os.listdir(carpeta))

    comprobar("una carpeta que no existe devuelve lista vacia",
              jornada.segmentos_del_dia(
                  os.path.join(carpeta, "no-existe")) == [])


def prueba_rutas_con_comillas_y_barras():
    """
    El archivo de lista de ffmpeg es su propio formato.

    La barra invertida de Windows es escape para el concatenador, y
    la comilla simple delimita el campo. Una ruta sin tratar rompe la
    union sin que se vea por que.
    """

    carpeta = dia_de_prueba([2, 2])
    segmentos = jornada.segmentos_del_dia(carpeta)

    lista = jornada._lista_para_ffmpeg(segmentos)

    try:
        texto = io.open(lista, encoding="utf-8").read()
    finally:
        os.remove(lista)

    comprobar("la lista no lleva barras invertidas",
              "\\" not in texto, texto)

    comprobar("y cada linea es un 'file'",
              all(l.startswith("file '") for l in texto.strip().splitlines()),
              texto)


def arbol_de_dias(dias):
    """Una carpeta de equipo con sus dias, como la deja el grabador."""

    raiz = tempfile.mkdtemp(prefix="equipo-")

    for fecha, cuantos in dias.items():

        anio, mes, dia = fecha.split("-")
        carpeta = os.path.join(raiz, anio, mes, dia)
        os.makedirs(carpeta, exist_ok=True)

        for i in range(cuantos):
            video_de_prueba(
                os.path.join(carpeta, "rec_%d.mp4" % (1790000000 + i * 900)),
                1
            )

    return raiz


def prueba_encuentra_los_dias():

    raiz = arbol_de_dias({"2026-11-07": 2, "2026-11-08": 1, "2026-11-09": 3})

    hallados = [f for f, _ in jornada.dias_con_segmentos(raiz)]

    comprobar("encuentra los tres dias",
              hallados == ["2026-11-07", "2026-11-08", "2026-11-09"],
              hallados)

    comprobar("una raiz vacia no da nada",
              jornada.dias_con_segmentos(
                  tempfile.mkdtemp(prefix="vacio-")) == [])


def prueba_los_dias_pasados_se_cierran_siempre():
    """
    Nadie va a escribir mas en un dia que ya paso.

    Es lo que recupera solo lo que quedo a medias: un apagon a las
    16:50, un equipo que estuvo apagado a las cinco, o los dias de
    octubre que nunca se unieron.
    """

    raiz = arbol_de_dias({"2026-11-07": 2, "2026-11-08": 1})

    listos = [f for f, _ in jornada.dias_por_cerrar(
        raiz, hoy="2026-11-09", dentro_de_la_franja=True)]

    comprobar("los dos dias pasados se cierran",
              listos == ["2026-11-07", "2026-11-08"],
              listos)

    comprobar("y se cierran aunque ahora mismo se este grabando",
              len(listos) == 2)


def prueba_hoy_no_se_cierra_dentro_de_la_franja():
    """
    El caso que importa: parar la continua a media manana.

    Sin esta condicion, media hora de pausa se leeria como "el dia
    termino" y se uniria una jornada con seis horas por delante.
    """

    raiz = arbol_de_dias({"2026-11-09": 2})

    # Los trozos son de hace mucho: solo la franja lo retiene.
    viejo = time.time() - 3 * 3600
    for f, c in jornada.dias_con_segmentos(raiz):
        for trozo in jornada.segmentos_del_dia(c):
            os.utime(trozo, (viejo, viejo))

    dentro = jornada.dias_por_cerrar(
        raiz, hoy="2026-11-09", dentro_de_la_franja=True)

    comprobar("dentro de la franja, hoy NO se cierra",
              dentro == [], dentro)

    fuera = [f for f, _ in jornada.dias_por_cerrar(
        raiz, hoy="2026-11-09", dentro_de_la_franja=False)]

    comprobar("fuera de la franja, hoy si se cierra",
              fuera == ["2026-11-09"], fuera)


def prueba_hoy_no_se_cierra_si_acaba_de_crecer():
    """
    Un segmento a medio escribir no se puede unir: el MP4 no tiene
    indice hasta que se cierra.
    """

    raiz = arbol_de_dias({"2026-11-09": 2})

    recien = jornada.dias_por_cerrar(
        raiz, hoy="2026-11-09", dentro_de_la_franja=False)

    comprobar("recien escrito, hoy no se cierra",
              recien == [], recien)

    listos = [f for f, _ in jornada.dias_por_cerrar(
        raiz, hoy="2026-11-09", dentro_de_la_franja=False,
        ahora=time.time() + jornada.QUIETUD + 1)]

    comprobar("pasada la quietud, ya si",
              listos == ["2026-11-09"], listos)


def prueba_un_dia_en_el_futuro_no_se_toca():
    """
    El reloj del equipo se movio. No se sabe si todavia va a escribir
    ahi, asi que no se toca.
    """

    raiz = arbol_de_dias({"2026-11-20": 2})

    listos = jornada.dias_por_cerrar(
        raiz, hoy="2026-11-09", dentro_de_la_franja=False,
        ahora=time.time() + jornada.QUIETUD + 1)

    comprobar("un dia en el futuro se deja en paz", listos == [], listos)


def principal():

    print("ffmpeg:", FFMPEG)
    print()

    prueba_une_y_cuadra()
    prueba_no_borra_si_no_cuadra()
    prueba_cuadra_detecta_el_truncado()
    prueba_retoma_un_borrado_a_medias()
    prueba_el_dia_no_se_come_a_si_mismo()
    prueba_una_carpeta_vacia_no_revienta()
    prueba_rutas_con_comillas_y_barras()
    prueba_encuentra_los_dias()
    prueba_los_dias_pasados_se_cierran_siempre()
    prueba_hoy_no_se_cierra_dentro_de_la_franja()
    prueba_hoy_no_se_cierra_si_acaba_de_crecer()
    prueba_un_dia_en_el_futuro_no_se_toca()

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
