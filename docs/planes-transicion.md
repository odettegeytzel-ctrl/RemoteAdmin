# Planes: transición, excepciones y fin de la prueba

Estado: **diseñado y probado en local. Nada activado.**

`MODO_CAPACIDADES = "observacion"`: el sistema anota lo que habría
bloqueado y deja pasar todo. Ninguna organización puede perder una
función mientras esto siga así.

---

## 1. Proteger lo que ya se usa

### El problema

Al activar los bloqueos, una organización que venga usando una función
que su plan no incluye la perdería de un día para otro. Eso no puede
pasar sin aviso.

### Qué señales son fiables

Hay que distinguir dos cosas que se confunden con facilidad:

| | Qué significa |
|---|---|
| **Configurada** | Hay un ajuste puesto que depende de esa capacidad. Alguien la preparó; no prueba que se use. |
| **Usada** | Hay constancia en la auditoría de una operación **completada**. |

Señales por capacidad:

| Capacidad | Configurada | Usada |
|---|---|---|
| `scheduling` | `recording_schedule.enabled = 1` | `recording.schedule` |
| `remote.actions` | *no deja estado persistente* | `device.lock`, `device.logoff`, `device.restart`, `device.shutdown`, `remote.session_start` |
| `recordings.store` | grabaciones archivadas | `recording.store` |
| `self_hosted` | `deployment_type = self_hosted` | *no es una operación* |

Dos precisiones que costaron un fallo cada una:

- **Las grabaciones antiguas cuentan.** Las filas anteriores a la
  columna `storage_state` llevan `NULL` y el servidor las trata como
  archivadas. Mirar solo la columna daba cero para una instalación
  veterana que sí usa la función.

- **`remote.actions` no deja rastro de configuración.** Solo se sabe
  por la auditoría. Si el registro se hubiera purgado, una
  organización que la use parecería no usarla.

### La trampa: no se gana una capacidad usándola

En modo observación, una función que el plan no incluye **no se
bloquea**: la operación se completa y queda registrada como uso
legítimo. Es lo correcto —observar no debe romper nada— pero
significa que cualquiera puede fabricarse uso de algo que no le toca
con solo usarlo.

Tampoco sirve excluir los registros `plan.feature_observed`: esos son
los *intentos*, y el problema está en los *éxitos*.

**La protección se decide sobre el uso anterior a una fecha de
corte**, que es el momento en que la política se anuncia. Lo
posterior no da derecho a nada.

```python
capacidades_en_riesgo(organization_id, hasta=FECHA_DE_CORTE)
```

El parámetro `hasta` es obligatorio para esta decisión. Sin él se
cuenta también el uso posterior al anuncio.

### Cómo se concede

**Nunca automáticamente.** El análisis solo lee: hay una prueba que
verifica que no escribe ninguna excepción.

1. Fijar la fecha de corte y anunciar la política.
2. Generar el informe de capacidades en riesgo, por organización.
3. **Una persona revisa y decide** caso por caso.
4. Conceder con dos claves juntas:
   - `plan.features_extra` — la capacidad
   - `plan.courtesy_until` — **hasta cuándo**
   - `plan.courtesy_note` — por qué

La fecha es lo que impide que la protección se vuelva permanente: al
vencer, la capacidad deja de concederse sola, sin que nadie tenga que
acordarse de retirarla.

Solo el operador de la plataforma puede concederlas. Un cliente no
puede, ni por `/api/settings` (lista blanca) ni por el endpoint de
plataforma (403). Queda en auditoría quién, cuándo y qué, **incluidos
los intentos rechazados**.

### Cómo se retira

`plan.features_extra` a vacío. No se pierde nada: retirar una
excepción no borra equipos, grabaciones ni usuarios. Solo deja de
concederse lo que concedía.

---

## 2. Fin de la prueba de 30 días

Estado: **diseñado, sin implementar y sin activar.**

Al vencer, la organización pasa a Free conservando todo.

### Reglas

- **Nada se borra.** Ni equipos, ni usuarios, ni grabaciones, ni
  archivos, ni configuración, ni historial.
- **Fecha explícita y auditable**: `plan.trial_until`, guardada, no
  calculada desde `created_at`. Así se puede ampliar sin inventar
  fechas de alta.
- **Idempotente**: se marca `plan.trial_converted_at`. Si ya está, no
  se repite la transición ni se duplica el registro.
- **En auditoría**: fecha, plan anterior, plan nuevo y motivo.

### Si supera los límites de Free

El estado que queda es «por encima del límite, sin poder crecer»:

- Se **conserva** todo lo que hay.
- **No se permite crecer**: `check_limit()` ya rechaza altas por
  encima del tope, y con exceso heredado seguiría rechazándolas sin
  tocar lo existente.
- Un estado explícito, `plan.over_limit_since`, para poder
  consultarlo y explicarlo en vez de deducirlo.
- Se informa de qué límites se superaron y cómo regularizar: reducir,
  o ampliar plan.

### Almacenamiento: el punto delicado

Aquí hay un conflicto que conviene decidir conscientemente.

**La subida del Agent no puede bloquearse por exceso de
almacenamiento.** Si se bloquea, el equipo acumula archivos que no
puede entregar y acaba perdiéndolos por su propia retención local.
Una restricción comercial no debería costar datos del cliente.

Por eso `/recordings/upload` está clasificada **sin capacidad**, y hay
una prueba que lo fija.

Lo que sí se puede restringir con seguridad es el **archivado a
petición** (`/recordings/{id}/store`): lo pide una persona desde el
panel y no tiene urgencia.

Esa asimetría —se puede dejar de archivar a petición, pero nunca de
recibir— **es una decisión comercial pendiente de aprobación**.

---

## 3. Excepción de `self_hosted`

El mecanismo está listo y probado. **No se ha concedido a nadie**, ni
en local ni en producción.

Para concederla a una organización concreta:

```
POST /api/organizations/{id}/plan
{"key": "plan.features_extra",  "value": "self_hosted"}
{"key": "plan.courtesy_until",  "value": "2027-01-01T00:00:00+00:00"}
{"key": "plan.courtesy_note",   "value": "<motivo>"}
```

Verificado: otra organización con el mismo plan **no la hereda**.

La cortesía comercial (`billing_mode`) y la capacidad técnica
(`features_extra`) son cosas separadas a propósito. Que una
organización no pague no decide qué puede hacer, y al revés.

---

## 4. Qué falta por decidir

1. **La fecha de corte** para la protección transitoria.
2. **Si el archivado a petición se restringe** por exceso de
   almacenamiento, mientras la entrega del Agent sigue libre.
3. **Cuánto duran** las protecciones transitorias antes de
   regularizar.
4. **Cuándo activar** `MODO_CAPACIDADES = "activo"`, y solo después
   de ver qué registra la observación con tráfico real.

Hasta entonces, nada cambia para nadie.
