# Preparar un instalador de prueba

La auditoria de la cadena y los pasos exactos, con lo comprobado en
cada punto. **Nada se ha instalado, distribuido ni publicado.**

> El `.exe` de `installer\dist\` se reconstruyo el **2026-10-28 a las
> 11:59**, con las guardas de identidad **y** con la actualizacion
> sobre un equipo ya registrado. Huella `4703784D...`, 79 959 782
> bytes. **Sin distribuir y sin instalar en ningun equipo.**
>
> Los dos anteriores (`234D6E3C...` de las 09:39, sin la logica de
> actualizacion, y `79598D31...` del 27-oct, sin las guardas) **no
> deben distribuirse**. Estan guardados aparte.
>
> El procedimiento de publicacion y de actualizacion esta en
> [actualizar-equipo-registrado.md](actualizar-equipo-registrado.md).

---

## Son dos canales distintos, y se confunden

| | paquete firmado | instalador `.exe` |
|---|---|---|
| para | equipos **ya instalados** | equipos **nuevos** |
| lo usa | `agent/updater.py` | una persona, con doble clic |
| se arma con | `tools/sign_agent_release.py` | `installer/build-installer.ps1` |
| deja | `releases/agent-X.Y.Z.zip` + `manifest.json` | `installer/dist/VEYRA-Instalador.exe` |
| necesita la clave Ed25519 | **si** | **no** |

**Para un instalador de prueba solo hace falta el segundo.** La clave
privada de firma no entra en juego: firma el manifiesto que leen los
Agents al auto-actualizarse, no el ejecutable.

> El `.exe` no lleva firma de codigo Authenticode. Windows mostrara el
> aviso de SmartScreen. Es lo que trata [firma-de-codigo.md](firma-de-codigo.md),
> y es una decision aparte.

---

## Que version instala

**El `.exe` empaqueta la carpeta `agent/` del arbol de trabajo**, con
`--add-data "$Agente;agent"`. Reconstruirlo hoy mete el codigo actual,
incluidos los dos commits de la supresion.

> El comentario de cabecera de `build-installer.ps1` dice lo
> contrario -«Tampoco lleva el Agent»- y **esta equivocado**. Lo
> desmiente la propia linea `--add-data` doce lineas mas abajo.
> Conviene corregirlo para que nadie decida a partir de el.

### El numero de version no cuadra

```
agent/version.py        AGENT_VERSION = "1.3.0"
releases/               agent-1.1.0.zip, agent-1.2.0.zip
releases/manifest.json  version 1.2.0
```

**No existe un paquete firmado 1.3.0.** Para el instalador de prueba
da igual -no mira el manifiesto-, pero significa dos cosas:

- un equipo instalado hoy dira **1.3.0** y el servidor ofrecera
  **1.2.0** como ultima version;
- los cambios de la supresion **no llegaran por auto-actualizacion** a
  los equipos ya instalados hasta que se publique un paquete firmado.

Decidir si 1.3.0 sigue siendo el numero correcto, ahora que lleva
cambios que no tenia cuando se puso, es previo a publicar.

### Lo que entra en el paquete firmado

`PACKAGE_FILES` (21 archivos) incluye `agent.py`, `scheduler.py`,
`owners.py` y `version.py`: **todo lo que tocan los dos commits**. No
hay que anadir nada a la lista.

`frontend/app.js` no esta, y es correcto: vive en el servidor.

---

## La credencial RAE

**No viaja en el paquete.** Se teclea al instalar, y por eso:

- `install-agent.ps1` la pide por pantalla como `SecureString`, o la
  acepta en `-EnrollmentToken`, o en un archivo que **lee, sobrescribe
  y borra**;
- rechaza que ese archivo sea un enlace o una carpeta, y lo limita en
  tamano;
- exige que empiece por `rae_`;
- la deja en `C:\ProgramData\RemoteAdmin\.env`, con ACL reducida a
  `SYSTEM:(R)` y `Administrators:(F)`.

**Quien valida es el servidor**, no el instalador: `rae_` solo es una
criba. `enrollment.organization_for_token()` decide la organizacion a
partir de la credencial, y el cuerpo de la peticion no puede influir.
El alta comprueba ademas el limite de equipos del plan.

Una vez que el equipo aparece en el panel, **la credencial se puede
revocar**: el Agent ya no la usa.

---

## La identidad del equipo

`C:\ProgramData\RemoteAdmin\config\identity.json` guarda
`device_id` y `agent_token`.

**El `device_id` lo asigna el SERVIDOR en el alta.** No se deriva del
hardware ni del nombre del equipo. Dos instalaciones = dos altas = dos
identificadores distintos, sin que haya que hacer nada.

Al registrarse:

- **sin identidad** -> manda la credencial RAE, y el servidor le
  asigna `device_id` y token individual;
- **con identidad** -> manda su token individual, y el servidor deriva
  la identidad **del token**, nunca del `device_id` que llegue en el
  cuerpo. Un equipo no puede hacerse pasar por otro.

---

## Instalar en una SEGUNDA computadora

**El caso normal no tiene truco.** El paquete no contiene ninguna
identidad: solo `agent\`, `installer\` y `runtime\`. Se copia el mismo
`.exe` a la segunda maquina, se teclea la misma credencial RAE -es de
la organizacion, no del equipo- y el servidor asigna un `device_id`
nuevo.

```
1. Copia el mismo VEYRA-Instalador.exe al segundo equipo.
2. Doble clic. Comprueba la direccion del servidor.
3. Teclea la MISMA credencial rae_...
4. Espera a que aparezca en Dispositivos, como equipo aparte.
5. Comprueba que los dos salen con device_id distinto.
```

### Las dos formas de duplicar una identidad

Ninguna la provoca el instalador; las dos son faciles de provocar a
mano:

**Copiar `config\identity.json` de un equipo a otro.** Entonces los
dos presentan el mismo token y el servidor los ve como el mismo
equipo: los latidos se pisan y las grabaciones se mezclan. No se
copia nunca esa carpeta.

**Clonar el disco o la maquina virtual con el Agent ya instalado.**
El clon nace con la identidad del original. Antes de encenderlo en
red hay que borrar `config\identity.json` del clon, y entonces se da
de alta como equipo nuevo.

### El caso que si muerde: un equipo ya enrolado en OTRO servidor

Si `identity.json` existe, `install-agent.ps1` **no pide credencial**:
da por hecho que el equipo ya esta de alta. Pero reescribe el `.env`
con la direccion nueva. El Agent arranca y se presenta al servidor
nuevo con un token que ese servidor no conoce:
`get_device_id_for_token` devuelve `None`, cae en la rama de alta,
`organization_for_token` tampoco lo reconoce, y responde **401**. Para
siempre, porque nadie le pedira jamas una credencial.

> **Afecta a este equipo de desarrollo.** Tiene identidad contra
> `127.0.0.1:8000`, con `device_id 774f538e82d2537d`. Instalar aqui un
> `.exe` que apunte a otro servidor cae exactamente en esto.

La salida, antes de instalar:

```powershell
Rename-Item "$env:ProgramData\RemoteAdmin\config\identity.json" "identity.ANTES.json"
```

Renombrar, no borrar: si hay que volver atras, se restaura. Despues
el instalador pedira la credencial y dara de alta un equipo nuevo.

---

## Que NO se pierde al instalar

Revisado linea por linea. `install-agent.ps1` solo borra dos cosas:

| borra | que es |
|---|---|
| el archivo de la credencial | si se paso con `-EnrollmentTokenFile`, a proposito |
| `runtime\` | el interprete, que vuelve a copiarse |

**No toca `config\`, `recordings\` ni `logs\`.** Reinstalar conserva
la identidad y las grabaciones del equipo.

**Si reescribe `.env`**, con la direccion y la credencial indicadas.
Conviene no confundirlo con el actualizador, que si lo conserva
(`PRESERVED = ("config", "recordings", "logs", ".env")`).

**Nada de esto toca a otros equipos.** La instalacion solo escribe en
el `ProgramData` de su maquina y da de alta un equipo en el servidor.
No borra ni modifica filas de otros equipos, ni sus grabaciones.

El desinstalador tambien conserva los datos: borra `agent\`,
`installer\` y `.env`, y solo con `-PurgeData` elimina identidad,
grabaciones y registros.

---

## Los pasos, cuando se autorice

Requisitos, los dos ya cumplidos en este equipo:

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python installer\build-runtime.py
```

El primero trae PyInstaller; el segundo arma el Python embebido, que
pesa unos 150 MB y no hace falta rehacer cada vez.

Construir:

```powershell
.\installer\build-installer.ps1 -Limpiar -Servidor "https://<el-panel>"
```

`-Servidor` precarga el campo de la ventana. Sin el sale vacio y hay
que dictar la direccion a quien este delante, que es donde se
equivoca la gente. **No se toma del `.env` de este repositorio a
proposito**: ahi pone `127.0.0.1:8000`.

Deja:

```
installer\dist\VEYRA-Instalador.exe
installer\dist\VEYRA-Instalador.sha256.txt
```

La constancia anota la huella del `.exe` y la del fuente: un
ejecutable no deja comprobar de que codigo salio.

### Antes de darlo por bueno

- Que la constancia tenga la fecha de hoy. **La que hay es del
  2026-10-21** y no lleva ninguno de estos cambios.
- Que el `.exe` abra, muestre la direccion correcta y que la
  comprobacion de conexion responda.
- Que `agent/version.py` diga lo que se quiere que diga.
- Instalarlo **en un equipo de pruebas**, nunca en uno en uso.
- Y ahi si, el recorrido de
  [prueba-real-supresion-horario.md](prueba-real-supresion-horario.md).

---

## Elegir la segunda computadora

### identity.json no dice de quien es

Solo guarda dos campos:

```json
{"device_id": "...", "agent_token": "..."}
```

**No deja constancia del servidor que la emitio.** Asi que mirando el
archivo es imposible saber si pertenece a este servidor o a otro. Esa
es la raiz del problema, no un descuido del procedimiento.

Hay dos vias para averiguarlo, y solo la segunda es concluyente:

| via | que dice | fiable |
|---|---|---|
| `.env` instalado, linea `REMOTEADMIN_SERVER` | a que direccion se configuro | **no**: se edita a mano, y puede faltar |
| preguntar al servidor con el token | si lo reconoce | **si** |

La segunda es `GET /api/agent/version`, que se autentica con el token
**individual** y no modifica nada: **200** = el servidor conoce ese
token; **401** = no es suyo.

> Se usa ese y no el latido a proposito: el latido actualiza la ultima
> conexion del equipo. Y tampoco se prueba contra `register`, porque
> un intento fallido ahi cuenta para el limite por IP y podria
> estorbar una instalacion de verdad.

### El comprobador

[`installer/comprobar-equipo.ps1`](../installer/comprobar-equipo.ps1)
hace las dos comprobaciones. **Solo lee**: no escribe, no borra, no
instala y no pide administrador. Se puede ejecutar en un equipo en
uso.

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\comprobar-equipo.ps1 -Servidor "https://remoteadmin.tail37c528.ts.net"
```

> **No viaja dentro del `.exe`.** `build-installer.ps1` empaqueta
> `install-agent.ps1` y `uninstall-agent.ps1` nombrandolos uno a uno,
> y este no esta en la lista; tampoco esta en `PACKAGE_FILES`. Hay
> que copiarlo aparte al equipo candidato.
>
> Tiene su logica -se ejecuta ANTES de tener nada instalado- pero
> conviene saberlo para no buscarlo donde no esta. Si se decide que
> viaje con el paquete, hay que anadirlo a las dos listas.

| salida | que significa | que hacer |
|---|---|---|
| `0` EQUIPO LIMPIO | sin identidad | instalar: pedira la credencial |
| `0` APTO | la identidad es de este servidor | reinstalar conserva device_id y grabaciones |
| `1` NO APTO | identidad de **otro** servidor | **no instalar** |
| `1` INDETERMINADO | no se pudo preguntar | **no instalar** hasta aclararlo |
| `2` | la URL lleva `/panel` o esta mal | corregir la direccion |

Comprobado el 2026-10-27 en ODET-TI, contra servidores reales:

- contra `https://remoteadmin.tail37c528.ts.net` -> **NO APTO**, salida
  1. Es correcto: este equipo tiene identidad de `127.0.0.1:8000`.
- contra `https://127.0.0.1:8000` con el servidor local en marcha ->
  **APTO**, salida 0.
- con `/panel` al final -> rechazado, salida 2.

**La rama EQUIPO LIMPIO no se ha ejercitado**: haria falta una maquina
sin Agent, que es justo la que no hay. Es la rama mas simple -un
`Test-Path` que falla- pero conviene saber que no esta probada.

### Que no se puede perder por accidente

Nada de esto escribe. Y el procedimiento que se propone **no borra
jamas** una identidad: la renombra.

```powershell
Rename-Item "$env:ProgramData\RemoteAdmin\config\identity.json" "identity.ANTES.json"
```

Renombrar deja volver atras. Borrar, no. Y aunque se renombre, lo que
se pierde no es poca cosa: el equipo se da de alta con un `device_id`
nuevo y **el servidor anterior lo da por desaparecido**; sus
grabaciones siguen ahi, atadas al identificador viejo. Por eso la
regla es elegir otro equipo, y apartar la identidad solo cuando se
sabe lo que cuesta.

---

## Las guardas de identidad

**Implementadas el 2026-10-27** en `install-agent.ps1`, seccion **2b**,
despues de detectar la identidad y **antes de copiar nada**. Si alguna
corta, el equipo queda exactamente como estaba.

El fallo que cierran: con identidad presente el instalador no pedia
credencial -daba por hecho que el equipo estaba de alta- pero si
reescribia el `.env` con la direccion nueva. El Agent acababa
presentandose con un token que el destino no conoce, y respondia 401
indefinidamente, porque nadie le volveria a pedir credencial. Y no se
notaba al instalar: la instalacion decia que todo habia ido bien.

### Guarda A, sin red

Compara la linea `REMOTEADMIN_SERVER` del `.env` **instalado** con el
destino. Si difieren, se detiene nombrando las dos direcciones.

Lee el `.env` de `$InstallDir`, no el de `$SourceDir`. Mirar el del
paquete era justo lo que dejaba pasar el fallo: son cosas distintas.

No es concluyente -el `.env` se edita a mano y puede faltar- pero es
gratis y atrapa el caso corriente al instante.

### Guarda B, concluyente

`GET {servidor}/api/agent/version` con el token existente.

- **401 o 403** -> se detiene: la identidad no es de ese servidor.
- **200** -> continua.
- **cualquier otra cosa** -> se detiene como **indeterminado**.

Ese endpoint se autentica con el token individual y **no modifica
nada**. Se eligio frente al latido, que actualiza la ultima conexion
del equipo; y frente a `register`, donde un intento fallido cuenta
para el limite por IP y estorbaria una instalacion de verdad.

**La validacion TLS se deja activada.** Un certificado que no valida
cae en indeterminado, que detiene la instalacion.

> **Detenerse ante lo indeterminado es deliberado.** Sin red, con un
> cortafuegos por medio o con el servidor caido no se sabe de quien es
> la identidad, y continuar a ciegas es exactamente como se llega al
> 401 permanente. Es mas molesto y mucho mas barato que lo otro.

### Lo que las guardas NO hacen

**No borran ni renombran `identity.json`.** Nunca, en ningun camino.
Se detienen y explican las dos salidas: instalar en otro equipo -lo
recomendable- o apartar la identidad a mano, con el comando escrito y
la advertencia de lo que cuesta.

### `-Forzar`

Se salta las dos. Hay que escribirlo: no se activa solo ni se sugiere
en ningun mensaje de error. Con identidad presente imprime un aviso
antes de continuar.

```powershell
.\installer\install-agent.ps1 -Server "https://..." -Forzar
```

Lo que hay que saber antes de usarlo: si la identidad es de otro
servidor, el Agent quedara en 401. Y si despues se aparta esa
identidad, el equipo se da de alta con un **`device_id` nuevo**: el
servidor anterior lo da por desaparecido y sus grabaciones historicas
se quedan atadas al identificador viejo.

### Pruebas

`tests/test_guardas_instalador.py`, **32/32**.

No reimplementa la logica: **extrae el bloque 2b del propio
`install-agent.ps1`** -entre los marcadores `# 2b.` y `# fin de 2b`- y
lo ejecuta tal cual. Si alguien cambia el bloque, la prueba corre el
texto nuevo; si borra los marcadores, falla en vez de pasar en verde
sobre nada.

Hace falta ese rodeo porque el script entero no se puede ejecutar en
una sesion sin privilegios: aborta antes, en la comprobacion de
administrador.

El servidor se simula con un `http.server` local en un puerto que
asigna el sistema. **No se toca la base real ni se registra ningun
equipo.**

Cubre: sin identidad, servidores que coinciden, servidores que
difieren, token valido, token rechazado, error de conexion, `-Forzar`,
y que en cada corte no se escribio nada. Mas las cuatro salidas del
comprobador.

Se metieron fallos a proposito para ver si se cazan:

| fallo introducido | |
|---|---|
| el 401 se trata como identidad buena | **cazado** |
| se quita el corte por token rechazado | **cazado** |
| se quita la guarda A entera | **cazado** |
| el indeterminado se da por bueno | **cazado** |
| la guarda A compara con el `.env` del paquete | **cazado** |
| la guarda borra la identidad por su cuenta | **cazado** |

Uno se escapo: sustituir `$reconocido = ($respuesta.StatusCode -eq 200)`
por `$reconocido = $true`. **No es un hueco de las pruebas**:
`Invoke-WebRequest` lanza excepcion con cualquier respuesta que no sea
2xx, asi que en ese punto las dos expresiones dan lo mismo. Es una
mutacion equivalente.

---

## Mejora pendiente: que identity.json diga de quien es

**No se ha hecho**, y es la unica que haria concluyente la guarda A sin
red.

Hoy el archivo guarda `device_id` y `agent_token`, nada mas.
`save_identity()` podria anotar tambien el servidor que emitio la
identidad, y entonces bastaria compararlo.

No va en este paso porque cambia el formato y hay que pensar la
compatibilidad: las identidades ya escritas no tienen ese campo, y su
ausencia debe tratarse como **"desconocido"**, nunca como
"incompatible". Si no, la primera version con el cambio dejaria sin
poder reinstalar a todos los equipos existentes.

---

## Que el token individual no se imprima

`test_produccion` comprobaba esto buscando la cadena
`identidad.agent_token` en todo el script. Con la guarda B empezo a
fallar: la guarda **usa** el token en la cabecera de autenticacion,
que es para lo que sirve, y la busqueda no distinguia usarlo de
imprimirlo.

**No se debilito la comprobacion. Se cambio por una que mira lo que
importa**, y que ademas es mas estricta.

### Por que importa tanto

Que el token salga por consola es malo. Que acabe en la
**transcripcion** lo es mas: ese archivo se queda en
`ProgramData\VEYRA-instalacion.log`, se envia cuando alguien pide
ayuda, y nadie lo revisa antes de mandarlo.

### Como se comprueba ahora

`impresiones_del_token()` en `tests/test_produccion.py` une las
continuaciones de linea -por acento grave y por parentesis sin
cerrar- y despues busca el token en cualquier enunciado que imprima:
`Write-Host`, `Write-Output`, `Write-Warning`, `Write-Error`,
`Write-Information` u `Out-Host`.

Y sigue el rastro de las variables: si una recibe el token y otra
recibe a la primera, las dos quedan marcadas. Hace falta porque este
script arma sus mensajes en arrays y los vuelca con un
`ForEach-Object`, y por ahi se colaria sin que ninguna linea con
`Write-Host` mencionara el token.

### Que el detector detecte

Un detector sin pruebas es un adorno: pasaria en verde igual si no
mirara nada. Lleva cinco fugas de mentira que **tiene** que cazar y
un uso legitimo que **no** debe marcar:

| caso | esperado | |
|---|---|---|
| impresion directa | caza | OK |
| partida con acento grave | caza | OK |
| partida entre parentesis | caza | OK |
| dentro de un array que luego se vuelca | caza | OK |
| encadenada por dos variables | caza | OK |
| token en la cabecera HTTP, sin imprimirlo | **no** marca | OK |

Y se inyectaron cuatro fugas **reales** en `install-agent.ps1`, con
el archivo restaurado despues:

| fuga inyectada | |
|---|---|
| impresion directa | **cazada** |
| partida con acento grave | **cazada** |
| dentro del array que se vuelca | **cazada** |
| por una variable intermedia | **cazada** |

### Y comprobado en ejecucion

Antes de cambiar la prueba se verifico que no hubiera fuga de verdad.
Se ejecuto el bloque con un token reconocible, con la transcripcion
abierta -como en una instalacion real- y tambien con `-Verbose`, por
si `Invoke-WebRequest` imprimiera cabeceras:

```
consola             token presente: False
consola -Verbose    token presente: False
transcripcion       token presente: False
```

---

## Pendientes que no hay que perder

**Paso 7 de la prueba real: sin hacer.** Que la franja del dia
siguiente vuelva a grabar sola. Exige dejar armada una programacion
que grabe una pantalla manana, y eso **no se ha hecho y necesita
autorizacion expresa**. Los pasos 1 a 6 pasaron el 2026-10-27.

**`test_control_remoto`: pendiente.** En la suite completa del
2026-10-26 dio 26/27. No se ha reproducido: seis pasadas posteriores
-cinco en solitario, una de ellas con la CPU cargada, y la suite
completa del 2026-10-27- dieron 27/27. Sin la salida del fallo no se
sabe ni que comprobacion cayo. **No esta resuelto: esta sin
reproducir.**

**`test_f4_validation`: fragil.** Copia la base de desarrollo real
como punto de partida (`shutil.copy(RAIZ/"remoteadmin.db", ...)`), asi
que su resultado depende de lo que haya dentro. Hoy da **45/46**
porque la prueba en equipo real anadio 162 grabaciones en estado
`local` al catalogar los `.mp4` que ya estaban en disco; antes solo
habia 11, todas `stored`. Contra el respaldo previo da **46/46**. El
codigo no tiene nada que ver, y **la base no se ha tocado para que
pase**.
