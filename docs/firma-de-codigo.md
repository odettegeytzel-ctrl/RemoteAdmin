# Firma de código del instalador

**Nada de esto está hecho.** Es la investigación previa para decidir.

---

## Por qué se plantea

`VEYRA-Instalador.exe` pesa 76 MB, lo produce PyInstaller en un solo
archivo y lleva dentro un intérprete de Python y ffmpeg. **Sin firma
digital**, eso dispara dos cosas en cualquier Windows actual:

- **SmartScreen** muestra «Windows protegió su PC» y esconde el botón
  de ejecutar detrás de «Más información». Mucha gente no pasa de ahí.
- **Defender** puede ponerlo en cuarentena. Los ejecutables de
  PyInstaller son un falso positivo conocido, precisamente por el
  patrón de descomprimirse y ejecutarse.

Y encaja con lo que viste: el instalador apareciendo como `removed`.
**No lo doy por probado** —la palabra no sale de VEYRA y no tengo
acceso a ese equipo— pero firmar lo descartaría de raíz.

## Lo que la firma NO arregla

Conviene saberlo antes de pagar.

Una firma dice **quién publica** el archivo, no que sea inofensivo.
Con un certificado OV, SmartScreen **sigue avisando** hasta que ese
certificado acumula reputación: descargas suficientes sin incidencias.
Puede tardar semanas. Solo un certificado **EV** da reputación desde
la primera firma.

Tampoco sustituye a la firma Ed25519 que ya tiene el proyecto: esa
protege el **paquete de actualización** y seguirá siendo la que impide
instalar código no autorizado en los equipos. Son dos cosas distintas
y hacen falta las dos.

---

## Opciones

| | OV | EV | Sin firmar |
|---|---|---|---|
| Coste anual | ~150–400 USD | ~300–700 USD | 0 |
| Aviso de SmartScreen | Sí, hasta ganar reputación | **No** | Siempre |
| Dónde vive la clave | Token USB o HSM | **Token USB o HSM, obligatorio** | — |
| Validación | Empresa registrada | Empresa + verificación reforzada | — |
| Plazo de emisión | 1–5 días | 1–3 semanas |  |

**Desde junio de 2023 ningún certificado de firma de código se entrega
como archivo.** La clave privada tiene que vivir en hardware
certificado: un token USB que envía la autoridad, o un HSM en la nube.
Eso cambia cómo se firma: no se puede automatizar sin más, porque hay
que tener el token conectado.

### Autoridades habituales

Sectigo, DigiCert, GlobalSign, SSL.com y Certum. **Certum** tiene una
opción para desarrollador individual notablemente más barata
(~100 USD/año con token), pero exige identificación personal y el
nombre que aparece es el de la persona, no el de NYRA.

### Firma en la nube

Azure Trusted Signing (~10 USD/mes) y los servicios de firma de
DigiCert y SSL.com evitan el token físico: la clave vive en su HSM y
se firma por API. **Azure Trusted Signing exige que la empresa tenga
al menos tres años de antigüedad verificable**, cosa que conviene
comprobar antes de contar con ello.

---

## Qué haría falta aquí

1. **Windows SDK**, que trae `signtool.exe`. Comprobado: **no está
   instalado en este equipo**. Se instala con el componente «Windows
   SDK Signing Tools for Desktop Apps», sin necesidad de Visual Studio.

2. **El certificado**, en su token o en el HSM.

3. **Un paso más en la construcción**, después de PyInstaller:

```powershell
signtool sign /tr http://timestamp.sectigo.com /td sha256 `
    /fd sha256 /a installer\dist\VEYRA-Instalador.exe

signtool verify /pa /v installer\dist\VEYRA-Instalador.exe
```

El **sellado de tiempo** (`/tr`) no es opcional: sin él, el ejecutable
deja de considerarse firmado el día que caduque el certificado,
aunque se firmara cuando era válido.

4. **Volver a firmar en cada versión.** Un `.exe` reconstruido pierde
la firma del anterior.

## Lo que no se va a hacer

- **Ningún certificado autofirmado.** No lo acepta SmartScreen, no lo
  acepta ningún equipo que no lo haya instalado antes a mano, y da una
  falsa sensación de estar resuelto.
- **Ninguna clave privada en el repositorio.** Ni la de firma de
  código, ni la Ed25519 del paquete. Esa regla ya está probada en
  `test_actualizacion`, que comprueba que no haya claves ni en el
  repositorio, ni en el paquete, ni en el servidor.

---

## Antes de pagar nada

Merece la pena confirmar que el problema es el que parece. Si el
`.exe` no llega a descargarse, la firma no cambiará nada.

```
1. chrome://downloads   ->  ¿dice «Eliminado»?
2. Seguridad de Windows -> Protección antivirus -> Historial
3. En el servidor:  ls -l /opt/remoteadmin/RemoteAdmin/releases/
                    sha256sum .../VEYRA-Instalador.exe
```

Si el archivo llega entero y con el SHA-256 correcto, y lo que molesta
es el aviso al abrirlo, entonces sí: lo que falta es la firma.

## Mi recomendación

**OV de Certum o Sectigo**, en torno a 150–250 USD al año. El aviso de
SmartScreen desaparecerá cuando el certificado acumule reputación, y
mientras tanto al menos el instalador dirá quién lo publica, que es lo
que un cliente empresarial va a mirar.

**EV solo si el aviso resulta inaceptable desde el primer día** —por
ejemplo, si lo instala personal no técnico sin nadie al lado. Cuesta
el doble y tarda semanas en emitirse.

**Decide tú.** Es un gasto recurrente y una decisión comercial, no
técnica.
