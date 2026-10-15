# Prueba manual en Windows

Lo que no se puede comprobar sin dos computadoras de verdad. Todo lo
demás está cubierto por las pruebas automáticas; esto es lo que falta.

**Antes de empezar:** el equipo administrado necesita el Agent con el
código actual. El publicado como 1.2.0 es anterior a estos cambios, así
que hay que instalarlo con `VEYRA-Instalador.exe` o publicar una
versión nueva. Si no, no se está probando esto.

| | |
|---|---|
| Equipo A | El del técnico, con el panel abierto |
| Equipo B | El administrado, con el Agent |

---

## 1. Grabación simultánea

Es la corrección más importante y la que más fácil es dar por buena
sin serlo.

**Preparación.** En B, horario de grabación continua de una franja que
incluya ahora mismo. Por ejemplo, si son las 14:20, de 14:00 a 18:00.

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Esperar a que entre la franja | El panel muestra la continua activa |
| 2 | En B, abrir el Administrador de tareas | **Un solo proceso** de captura. Si hubiera dos, se estaría grabando la pantalla dos veces |
| 3 | Pulsar **Iniciar grabación** manual | Ambas constan activas. **El vídeo no se corta** |
| 4 | Anotar la hora | Hace falta para el paso 8 |
| 5 | Esperar 3 minutos | |
| 6 | Pulsar **Detener grabación** manual | **La continua SIGUE.** Es el punto que antes fallaba: detener la manual mataba la continua |
| 7 | Esperar a que termine la franja | La grabación se detiene |
| 8 | En Grabaciones, columna **Modalidad**, pulsar «Ver» | El archivo que cubre el paso 4 debe decir **Programada N m** y **Manual 3 m** |

**Lo que invalida la prueba:** que al detener la manual se pare el
vídeo, que aparezcan dos procesos de captura, o que un archivo se
atribuya entero a una sola modalidad cuando ambas estuvieron activas.

## 2. Los archivos MP4

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | En B, abrir `C:\ProgramData\RemoteAdmin\recordings\AAAA\MM\DD\` | Los segmentos, de ~15 minutos |
| 2 | Abrir el que cubre el solape | Se reproduce entero, sin cortes |
| 3 | Comparar su duración con la del panel | Deben coincidir |

**Hay que saberlo:** durante el solape **el vídeo es uno**. No hay dos
archivos, uno manual y otro continuo: es la misma pantalla en el mismo
instante. La columna Modalidad dice qué parte corresponde a cada una.

## 3. Reconexión sin duplicados

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Con la continua grabando, desconectar la red de B | El panel marca B sin conexión |
| 2 | Esperar 2 minutos | B **sigue grabando**: la decisión la toma su reloj |
| 3 | Reconectar | B vuelve a aparecer |
| 4 | Mirar Grabaciones | **Ninguna grabación duplicada** |
| 5 | Pulsar «Ver» en Modalidad | Los tramos de la desconexión también están |
| 6 | Reiniciar el servicio del Agent en B a mitad de franja | Al volver retoma, **sin una segunda captura** |

## 4. Información del equipo

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Reiniciar el Agent en B | |
| 2 | Abrir la ficha de B en el panel, **sin pulsar nada** | Los doce campos con datos reales: usuario, procesador, CPU, versión, arquitectura, fabricante, modelo, RAM, almacenamiento |
| 3 | Comparar con `msinfo32` en B | Fabricante y modelo deben coincidir |
| 4 | Pulsar **Actualizar información** | Los valores vivos —RAM disponible, disco libre— cambian |

El paso 2 es el que importa: antes esos campos solo se llenaban si
alguien pulsaba el botón, y un equipo donde nunca se pulsó decía «Sin
información» para siempre.

**Si algún campo sigue vacío**, anotar cuál y qué devuelve en B:

```powershell
Get-CimInstance Win32_ComputerSystem | Select Manufacturer, Model
```

Si ahí tampoco sale, es que Windows no lo expone en ese equipo. Se
muestra «No disponible», no se inventa.

## 5. Teclado remoto

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Abrir el escritorio remoto de B | El aviso dice **«Teclado → equipo remoto»** |
| 2 | Abrir el Bloc de notas en B y escribir | El texto aparece en B, **no en A** |
| 3 | Escribir mayúsculas, números y `@ ñ ´` | Todos llegan bien |
| 4 | **Pulsar un botón de la barra** (Windows) | Se abre el Inicio **de B** |
| 5 | Volver a escribir, sin tocar nada más | **Sigue llegando a B.** Este era el fallo: tras pulsar un botón el teclado dejaba de llegar |
| 6 | Pulsar un tirador de redimensionado y escribir | Sigue llegando |
| 7 | Mantener **Ctrl** y hacer clic fuera de la ventana | El aviso cambia a «Pulsa la pantalla para escribir» |
| 8 | En B, comprobar que Ctrl **no quedó pulsado** | Escribir una letra en B debe dar la letra, no un atajo |
| 9 | Cerrar con la **X** y escribir en A | No llega nada a B |

**Lo que no hay que probar porque no puede funcionar:** Alt+Tab,
Ctrl+W, Ctrl+Esc y la tecla Windows desde el teclado físico. El shell
de A las atiende antes que el navegador. Para eso están los botones.

## 6. Permisos

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Crear un subadministrador **sin** `remote.control` | |
| 2 | Entrar con él e intentar abrir el escritorio remoto | Se rechaza |
| 3 | Concederle el permiso y reintentar | Funciona |
| 4 | Con él, pulsar «Actualizar información» | Funciona si tiene `devices.view` |

## 7. Mantenimiento

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | **Avisar mantenimiento** | En B aparece la ficha arriba a la derecha |
| 2 | Esperar 10 segundos | Se encoge a la barra pequeña. **El mantenimiento sigue activo** |
| 3 | Pulsar la barra en B | Se vuelve a abrir |
| 4 | Pulsarlo otras tres veces desde el panel | **No se abren más ventanas** |
| 5 | Trabajar en B por debajo del aviso | Se puede pulsar lo que hay detrás |
| 6 | Cerrar el navegador en A **en seco** | A los ~5 minutos el aviso desaparece solo |

---

## Qué anotar si algo falla

El paso, lo que se esperaba, lo que pasó, y en B:

```
C:\ProgramData\RemoteAdmin\logs\agent.log
```

Las líneas `[grabacion]` dicen quién sostiene la captura tras cada
cambio, que es justo lo que hace falta para entender los pasos 1 y 3.
