# Credenciales de alta: propuesta

**Esto es una propuesta. No hay nada implementado.** Ningún archivo de
`backend/enrollment.py` ni de la base de datos se ha tocado.

---

## Lo que hay hoy

Conviene fijar los hechos antes de diseñar, porque el problema que
parecía haber no es el que hay.

| | Credencial de alta | Token del equipo |
|---|---|---|
| Aspecto | `rae_…` | token individual |
| Para qué | Registrar un equipo **nuevo** | Que un equipo **ya registrado** se conecte |
| Dónde vive | En el `.env` del instalador | En `config\identity.json` |
| **¿Caduca?** | **Sí, si se le pone fecha** | **No. Nunca** |
| Se anula | Al caducar o al revocarla | Solo revocándola a mano |

El token del equipo se guarda en `devices` como `agent_token_hash`,
con `agent_token_issued_at` y `agent_token_active`. **No existe
ninguna columna de caducidad** ([backend/devices.py](../backend/devices.py)).

**Ningún equipo instalado deja de funcionar al cabo de 30 días.** Lo
que caduca es la credencial con la que se dan de alta equipos nuevos.

Dos cosas que ya están bien y no hay que tocar:

- De la credencial solo se guarda el **SHA-256**. Quien lea la base no
  puede fabricar una válida.
- El paquete del instalador sale con **`AGENT_TOKEN=` vacío**
  ([backend/packaging.py:114](../backend/packaging.py)). El secreto no
  viaja dentro del ZIP: lo escribe el instalador en el momento.

Y una carencia: **`uses` es un contador, no un límite.** Cuenta cuántas
altas se han hecho, pero nada le impide seguir subiendo. Hoy no existe
el «límite de usos» que pide el requisito.

---

## El problema real

Si se mete una credencial de 30 días en un instalador y se distribuye,
a los 30 días el instalador deja de servir. Alguien tiene que entrar al
panel, crear otra y rehacer el paquete. Eso es lo que bloquea la
instalación automatizada.

Y la salida fácil —una credencial permanente y sin límite— es
exactamente lo que no queremos: un secreto eterno, copiado en cada
paquete distribuido, que da de alta equipos en una organización para
siempre.

---

## Por qué una credencial de alta no puede renovarse a sí misma

Es la restricción que manda sobre todo el diseño.

Renovar algo exige presentarlo primero. Una credencial **caducada ya no
autoriza nada**, así que no puede pedir su propia sustituta: si pudiera,
la caducidad no significaría nada y tendríamos una credencial eterna
con otro nombre.

Tampoco sirve que la renueve el Agent. Un Agent instalado **ya no tiene
la credencial de alta** —la usó una vez y lo que guarda es su token
individual—, y aunque la tuviera, darle poder para emitir credenciales
de alta convertiría cualquier equipo comprometido en una puerta para
registrar equipos nuevos.

**Conclusión: quien emite tiene que ser el servidor, por su cuenta, sin
que nadie le presente la credencial vieja.**

---
## Decisiones tomadas

| | |
|---|---|
| Usos por defecto | **25**, configurable por organización |
| Instalación | **Exige conexión**, con reintentos seguros |
| Identificador de despliegue | **Uno por paquete**, revocable individualmente, atado a una organización |
| Renovación automática | **Activada por defecto**, configurable y desactivable |

El resto del documento desarrolla esas cuatro.

---

## La propuesta

### 1. Perfil de alta por organización

Tabla nueva `enrollment_profiles`:

| campo | por defecto | qué es |
|---|---|---|
| `organization_id` | — | a quién pertenece |
| `duration_days` | `30` | cuánto vive cada credencial |
| `max_uses` | `25` | **cuántas altas admite** |
| `auto_renew` | `1` (**activada**) | si el servidor emite la sucesora |
| `renew_before_days` | `7` | con cuánta antelación |

Nada permanente, nada ilimitado, y los cuatro valores se cambian desde
el panel.

### 2. Emisión segura: quién puede y quién no

**Tres caminos. Ninguno acepta la credencial vieja como prueba.**

**a) Una persona, desde el panel.** Sesión válida con permiso
`devices.manage`. Es lo de hoy, sin cambios.

**b) El servidor, por su cuenta.** Una tarea diaria recorre los
perfiles con `auto_renew` y emite la sucesora de toda credencial a la
que le queden menos de `renew_before_days`.

Aquí **no hay solicitud que autenticar porque no hay solicitud**: es
código del servidor leyendo su propia base. Nada externo puede
disparar una emisión. Esa es la propiedad que lo hace seguro, y la
razón de que no exista una API de renovación.

**c) El instalador, presentando su identificador de paquete.** Es el
camino nuevo y el único expuesto a la red. Se detalla abajo.

Toda emisión queda en auditoría, con actor `sistema` en el caso (b).

### 3. El identificador de despliegue: uno por paquete

Al generar un instalador, el servidor crea una fila en
`deployment_ids`:

| campo | qué |
|---|---|
| `deployment_id` | valor aleatorio de 32 bytes, **visible**, va en el paquete |
| `organization_id` | a qué empresa entrega credenciales |
| `active` | **revocación individual de este paquete** |
| `created_at`, `label` | quién lo generó y para qué |
| `last_used_at`, `uses` | para detectar un paquete que se usa de más |

**Qué es y qué no es.** No autoriza a registrar ningún equipo: solo
permite **pedir** la credencial vigente de su organización. Pero sí es
un dato con poder, y el diseño lo trata como tal.

**Revocar un paquete** es poner su `active = 0`. Deja de entregar
credenciales **al instante**, sin tocar los demás paquetes, sin tocar
las credenciales ya emitidas y **sin tocar un solo equipo ya
registrado**. Eso es lo que se gana frente a un identificador por
organización: si un instalador acaba donde no debe, se anula ese y
nada más.

### 4. Cómo se evita que ese identificador dé credenciales sin control

Es la pregunta más importante del diseño. Cinco capas, y **ninguna
basta sola**:

**1. Límite por IP.** `backend/ratelimit.py` ya existe y tiene ámbitos
separados. Se añade uno, `deploy`: **5 peticiones por hora y por IP**,
contando todas y no solo las fallidas. Va en su propio ámbito, igual
que `recovery`, porque bloquear la emisión no debe bloquear el acceso
al panel.

**2. Límite por identificador.** El de IP no basta: un atacante cambia
de IP. `deployment_ids.uses` cuenta las peticiones de cada paquete con
su propio techo horario. Un paquete que pide en bucle **se desactiva
solo** y queda en auditoría.

**3. El techo real: `max_uses`.** Aunque alguien obtuviera la
credencial, solo sirve para **25 altas**. Pasadas esas, no registra
nada más. Es el límite que convierte una fuga en un incidente acotado.

**4. Nunca se entrega una credencial nueva.** La petición devuelve la
**vigente**, no emite una. Pedirla mil veces da mil copias de la
misma, con su misma caducidad y su mismo contador. **No hay forma de
multiplicar credenciales pidiéndolas.**

**5. Todo queda registrado.** Cada entrega apunta el `deployment_id`,
la IP y el instante. Un paquete que entrega credenciales a las cuatro
de la mañana desde otro país es visible.

Y lo que no conviene perder de vista: **el peor caso de todo esto es
que alguien registre equipos falsos en una organización**. Molesto,
visible en el inventario y reversible dando de baja. **Nunca es acceso
a los equipos que ya existen**, porque su token individual no sale de
ahí.

### 5. Límite de usos: hoy no existe

```sql
UPDATE enrollment_tokens SET uses = uses + 1, last_used_at = ? WHERE id = ?
```

[backend/enrollment.py:244](../backend/enrollment.py) **cuenta los usos
pero no los limita.** No hay `max_uses` ni nada que lo compruebe: el
contador sube y la credencial sigue valiendo. El «límite de usos» del
requisito no está implementado en absoluto.

Y hay un detalle que hay que arreglar en el mismo cambio: `consume()`
lee la fila y después la actualiza, en **dos sentencias separadas sin
transacción**. Dos altas simultáneas pueden leer `uses = 24` las dos y
pasar las dos. Con un contador suelto da igual; **con un límite, no.**
La comprobación y el incremento tienen que ir en una sola sentencia
condicional:

```sql
UPDATE enrollment_tokens
   SET uses = uses + 1, last_used_at = ?
 WHERE id = ? AND active = 1
   AND (expires_at IS NULL OR expires_at > ?)
   AND (max_uses IS NULL OR uses < max_uses)
```

Y se acepta el alta **solo si `rowcount == 1`**. Decide la base, y no
hay carrera posible.

### 6. El instalador no lleva ningún secreto

```
generar el paquete   ->  ZIP con el deployment_id. Sin credencial.
descargarlo          ->  reutilizable siempre; no caduca
ejecutarlo           ->  pide al servidor la credencial vigente
registrar el equipo  ->  la canjea por su token individual
```

Hoy el paquete ya sale con `AGENT_TOKEN=` vacío
([backend/packaging.py:114](../backend/packaging.py)) y lo rellena el
instalador. Eso se mantiene; lo que cambia es de dónde sale el valor.

**Reintentos seguros.** Si la petición falla, el instalador reintenta
con espera creciente —5 s, 15 s, 45 s, hasta tres veces— y si aun así
no lo consigue, **se detiene y lo dice**. No instala un Agent sin
identidad ni deja un servicio a medias. Volver a lanzarlo retoma desde
donde estaba: pedir la credencial es idempotente —devuelve la
vigente—, así que reintentar no consume usos ni emite nada.

**Un uso se gasta cuando el equipo se registra de verdad, no cuando se
pide la credencial.** Esa distinción es lo que hace que los reintentos
sean seguros.

### 7. Caducidad, renovación y revocación

- **Caducidad**: `expires_at`, configurable vía `duration_days`.
- **Renovación**: activada por defecto, con 7 días de antelación. Si
  falla una noche hay **siete oportunidades** antes de quedarse sin
  credencial; por eso el margen no es de un día.
- **Al renovar, la nueva convive con la vieja** hasta que esta caduca.
  No se revoca anticipadamente: hacerlo rompería cualquier instalación
  en curso.
- **Revocación**: `active = 0`, inmediata, a tres niveles — una
  credencial, un paquete, o el perfil entero.

### 8. Qué pasa cuando algo falla

| situación | qué ocurre |
|---|---|
| El servidor no responde al instalar | Tres reintentos; si no, **se detiene y lo dice**. Nada a medias |
| La renovación falla una noche | Se reintenta al día siguiente. Siete oportunidades |
| La credencial caducó y no hay sucesora | **Las altas nuevas se rechazan.** Los equipos ya registrados no se enteran |
| `auto_renew` desactivada | No se emite nada. El panel muestra la fecha y avisa con antelación |
| `max_uses` agotado | No se registran más equipos. Alguien decide si sube el límite |
| Paquete revocado | Ese instalador deja de funcionar. Los demás, intactos |

**La peor avería posible es «no se pueden dar de alta equipos
nuevos».** Nunca «los equipos existentes se desconectan». Es
deliberado: la separación entre las dos credenciales es lo que
garantiza que un fallo aquí no toque lo que ya funciona.

### 9. Secretos

- **Repositorio**: nada. El `.env` del paquete sale vacío.
- **Instalador**: ninguna credencial. Solo el `deployment_id`.
- **Base**: solo el SHA-256 de la credencial. El `deployment_id` sí en
  claro, porque no es un secreto y hay que poder buscarlo.
- **Registros y auditoría**: **nunca el valor en claro**, ni un
  fragmento. Se registra el `id`.
- **Interfaz**: el valor se enseña **una vez**, al crearlo, y no se
  puede volver a consultar. Es lo que ya hace `create_token`.

### 10. Los equipos ya registrados

**No se tocan.** Ni su token, ni su identidad, ni su conexión. Su token
individual no caduca y este diseño no lo roza.

Migración aditiva: dos tablas nuevas y una columna nueva, todas con
valor por defecto. Las credenciales existentes siguen funcionando; sin
perfil, se comportan como hoy. Revertir deja las tablas sin usar, y
SQLite no se queja de columnas que nadie nombra.

---

## Riesgos pendientes

**1. El `deployment_id` es superficie nueva expuesta a la red.** Es el
punto más delicado y el que más pruebas necesita. Las cinco capas lo
acotan, pero antes no había **ningún** endpoint que entregara
credenciales, y ahora lo habrá.

**2. El instalador necesita red antes que ahora.** En una instalación
desatendida sin conectividad, falla antes. Es preferible a instalar un
Agent que no podrá registrarse, pero es un cambio de comportamiento.

**3. Una tarea diaria más.** Acabamos de ver lo que pasa cuando una de
estas falla en silencio: la de retención llevaba desde el multiempresa
sin publicar su resumen por un `KeyError`. Esta tiene que registrar lo
que hace **y lo que no puede hacer**, y dejarlo en auditoría.

**4. No elimina el trabajo manual del todo.** Si una organización agota
sus 25 usos, alguien decide si sube el límite. Es intencionado: un
límite que se amplía solo no es un límite.

**5. Depende del reloj del servidor.** `expires_at` es un instante
absoluto. Un servidor con la hora mal adelanta o retrasa todas las
caducidades.

**6. La carrera de `consume()` existe hoy.** Sin límite de usos es
inofensiva. En cuanto se añada el límite hay que arreglarla **en el
mismo cambio**, no después.

---

## Archivos que habría que tocar

Nada de esto está hecho.

| archivo | qué |
|---|---|
| `backend/database.py` | `enrollment_profiles`, `deployment_ids`, columna `max_uses` |
| `backend/enrollment.py` | perfiles, límite de usos, `consume()` atómico, emisión de la sucesora |
| `backend/ratelimit.py` | ámbito `deploy` |
| `backend/main.py` | tarea diaria; endpoint del instalador; ajustes del perfil; revocar paquete |
| `backend/packaging.py` | crear el `deployment_id` y meterlo en el paquete |
| `installer/install-agent.ps1` | pedir la credencial, con reintentos |
| `frontend/` | caducidad, aviso, interruptor, revocar credencial y paquete |
| `tests/` | renovación, cercanía a la caducidad, fallo de red, expiración, revocación, renovación desactivada, reinicio del servicio, **límite de usos bajo concurrencia**, límite de peticiones |

---

## Lo que falta aprobar

1. **¿5 peticiones por hora y por IP** es razonable para cómo instalas?
   Si despliegas varios equipos seguidos desde la misma red se queda
   corto y conviene subirlo.
2. **¿Un paquete debe caducar por sí mismo?** Propongo que no: vive
   hasta que lo revoques. La alternativa es darle fecha de fin.
3. **¿El aviso de caducidad, a quién y cómo?** Hay correo configurado
   (`backend/mailer.py`). Puede ser solo en el panel, o también por
   correo.
