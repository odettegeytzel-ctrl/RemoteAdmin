/*
La cola del mouse remoto, con latencia de verdad.

Por que existe esta prueba
--------------------------

Por Internet el cursor remoto iba con retraso y a saltos; en la misma
red se portaba bien. La causa no era el ancho de banda: la cola
conservaba el ORDEN pero no limitaba CUANTOS movimientos podian estar
esperando respuesta a la vez.

Con 155 ms de ida y vuelta y un movimiento generado cada 16 ms se
encolan unos doce por cada uno que llega a su destino. La cola crece
sin tope y el cursor acaba repitiendo un trazo de hace medio minuto.
En la misma red (3 ms) nunca se formaba cola, y por eso no se veia.

Que se comprueba aqui
---------------------

  - que con latencia alta NO se acumulan movimientos obsoletos;
  - que el retraso se mantiene en el orden de una ida y vuelta, en
    vez de crecer sin parar;
  - que down, up y los clics NUNCA se descartan ni se adelantan;
  - que el ultimo punto de un arrastre sale ANTES del 'up', porque
    es el que decide donde se suelta;
  - que al cambiar de equipo no se envia un movimiento del anterior.

No se reimplementa nada: se extrae el codigo real de frontend/app.js
y se ejecuta con un reloj y un fetch simulados.

    node tests/navegador/mouse-remoto.mjs [app.js] [referencia.js]

Sin argumentos comprueba frontend/app.js.

El segundo argumento es opcional: un app.js ANTERIOR al parche. Si
se da, se mide tambien cuanto mejora, que es lo que demostro en su
dia que el arreglo servia para algo. Sin el se comprueba solo que el
comportamiento correcto sigue en pie, que es lo que importa en cada
ejecucion posterior.
*/

import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";

const RUTA = process.argv[2] || "frontend/app.js";
const RUTA_REFERENCIA = process.argv[3] || null;

const FUENTE_PROPUESTA = readFileSync(RUTA, "utf8");

const FUENTE_REFERENCIA = RUTA_REFERENCIA
    ? readFileSync(RUTA_REFERENCIA, "utf8")
    : null;

if (!FUENTE_PROPUESTA.includes("function enviarMovimiento(")) {
    console.error(
        `${RUTA} no lleva el arreglo de la cola del mouse `
        + "(falta enviarMovimiento)."
    );
    process.exit(2);
}

const resultados = [];

function comprobar(nombre, condicion, detalle = "") {
    resultados.push([nombre, Boolean(condicion), String(detalle)]);
}


/* ------------------------------------------------------------------
   Extraer una funcion del archivo por su nombre.

   Se cuentan llaves desde la apertura, de modo que vale con el
   estilo del archivo (cuerpos sin sangrar). Si no aparece, la
   prueba falla en vez de pasar en silencio: justo lo que haria una
   comprobacion inutil si alguien renombra la funcion.
------------------------------------------------------------------ */
function extraer(fuente, nombre) {

    const inicio = fuente.indexOf(`function ${nombre}(`);

    if (inicio < 0) {
        throw new Error(`No se encontro function ${nombre}(`);
    }

    let i = fuente.indexOf("{", inicio);
    let nivel = 0;

    for (; i < fuente.length; i++) {
        if (fuente[i] === "{") nivel++;
        else if (fuente[i] === "}") {
            nivel--;
            if (nivel === 0) return fuente.slice(inicio, i + 1);
        }
    }

    throw new Error(`Llaves sin cerrar en ${nombre}`);
}


/* ------------------------------------------------------------------
   Un banco de pruebas con reloj y red simulados.
------------------------------------------------------------------ */
function banco({ rtt, fuente }) {

    let ahora = 0;
    let siguienteId = 1;
    const temporizadores = new Map();

    // Para provocar fallos de red en una accion concreta
    const fallarA = new Set();
    let fallarTodo = false;

    const enviados = [];        // {accion, payload, t}
    const llegados = [];        // {accion, payload, nacido, t}

    const ctx = {
        console,

        // --- reloj ---
        Date: { now: () => ahora },

        setTimeout(fn, ms) {
            const id = siguienteId++;
            temporizadores.set(id, { cuando: ahora + ms, fn });
            return id;
        },

        clearTimeout(id) {
            temporizadores.delete(id);
        },

        // --- red ---
        fetch(url, opciones) {
            const accion = url.split("/").pop();
            const payload = JSON.parse(opciones.body);
            const nacido = payload.t;

            enviados.push({ accion, payload, t: ahora });

            if (fallarA.has(accion) || fallarTodo) {
                fallarA.delete(accion);
                return Promise.reject(new Error("red caida"));
            }

            return new Promise(res => {
                const id = siguienteId++;
                temporizadores.set(id, {
                    cuando: ahora + rtt,
                    fn: () => {
                        llegados.push({ accion, payload, nacido, t: ahora });
                        res({ ok: true });
                    }
                });
            });
        },

        // --- lo que el codigo del panel espera encontrar ---
        selectedDevice: { device_id: "EQUIPO-A" },
        isDragging: false,
        pressedMouseButton: "left",
        activePointerId: null,

        mouseQueue: Promise.resolve(),
        pendingMove: null,
        moveTimer: null,
        lastMouseMoveTime: 0,
        movimientosEnVuelo: 0,
    };

    createContext(ctx);

    const piezas = ["postMouse", "flushPendingMove", "queueMouseMove",
                    "finishDrag"];

    // Solo la version con el parche la tiene. Asi la misma prueba
    // sirve para los dos archivos sin saber cual es cual.
    if (fuente.includes("function enviarMovimiento(")) {
        piezas.push("enviarMovimiento");
    }

    for (const nombre of piezas) {
        runInContext(extraer(fuente, nombre), ctx);
    }

    async function avanzar(hasta) {

        let guardia = 0;

        while (guardia++ < 500000) {

            // Primero las microtareas: postMouse encadena con .then,
            // de modo que el fetch no llega a llamarse -ni registra
            // su temporizador- hasta que la cola de microtareas
            // corre. Buscar temporizadores antes daria cero y la
            // prueba terminaria sin haber entregado nada.
            await new Promise(r => setImmediate(r));

            const pendientes = [...temporizadores.entries()]
                .filter(([, t]) => t.cuando <= hasta)
                .sort((a, b) => a[1].cuando - b[1].cuando);

            if (!pendientes.length) break;

            const [id, t] = pendientes[0];
            temporizadores.delete(id);
            ahora = t.cuando;
            t.fn();
        }

        ahora = Math.max(ahora, hasta);
    }

    return { ctx, enviados, llegados, avanzar,
             reloj: () => ahora,
             saltar: ms => { ahora += ms; },
             fallarLaProxima: accion => fallarA.add(accion),
             cortarRed: valor => { fallarTodo = valor; } };
}


/* ==================================================================
   1. Con latencia alta no se acumulan movimientos obsoletos
================================================================== */

async function pruebaAcumulacion(fuente) {

    const b = banco({ rtt: 155, fuente });

    b.ctx.isDragging = true;

    // Tres segundos moviendo a 120 Hz, como un arrastre real
    for (let t = 0; t <= 3000; t += 1000 / 120) {
        b.ctx.setTimeout(() => {
            b.ctx.queueMouseMove({ x: t | 0, y: t | 0, t: b.reloj() });
        }, t - b.reloj() > 0 ? t - b.reloj() : 0);
    }

    await b.avanzar(3000);

    const entregadosAlSoltar = b.llegados.length;

    // Cuanto sigue trabajando DESPUES de soltar el raton: ahi se ve
    // el atasco, porque la cola encadena y solo hay una peticion
    // viva a la vez.
    await b.avanzar(3000 + 300000);

    const ultimo = b.llegados[b.llegados.length - 1];
    const retraso = ultimo ? ultimo.t - ultimo.nacido : 0;
    const vaciar = ultimo ? ultimo.t - 3000 : 0;

    return {
        enviados: b.enviados.length,
        entregadosAlSoltar,
        retraso,
        vaciar
    };
}

const conParche = await pruebaAcumulacion(FUENTE_PROPUESTA);

comprobar(
    "la cola se vacia casi al instante al soltar el raton",
    conParche.vaciar <= 500,
    `${Math.round(conParche.vaciar)} ms despues de soltar`
);

comprobar(
    "el retraso se queda en el orden de una ida y vuelta",
    conParche.retraso <= 500,
    `${Math.round(conParche.retraso)} ms (ida y vuelta: 155 ms)`
);

comprobar(
    "tres segundos moviendo no generan mas de un envio por ida y vuelta",
    conParche.enviados <= 3000 / 155 + 5,
    `${conParche.enviados} envios en 3 s`
);

// Solo si se dio un app.js anterior al parche: cuanto mejora.
if (FUENTE_REFERENCIA) {

    const sinParche = await pruebaAcumulacion(FUENTE_REFERENCIA);

    comprobar(
        "la referencia sin arreglar seguia moviendo el cursor tras soltar",
        sinParche.vaciar > 10000,
        `${Math.round(sinParche.vaciar)} ms de cola pendiente`
    );

    comprobar(
        "y su ultimo movimiento llegaba con un retraso enorme",
        sinParche.retraso > 5000,
        `${Math.round(sinParche.retraso)} ms`
    );

    comprobar(
        "el arreglo envia muchos menos movimientos que la referencia",
        conParche.enviados < sinParche.enviados / 4,
        `${conParche.enviados} frente a ${sinParche.enviados}`
    );
}


/* ==================================================================
   1b. Al parar, la ULTIMA posicion llega

   Es el riesgo propio del resumen: si se descarta un movimiento
   porque hay otro en vuelo, alguien tiene que mandarlo despues. Si
   nadie lo hace, el cursor remoto se queda donde estaba hace una
   ida y vuelta y no llega nunca a donde se dejo el raton.
================================================================== */

async function pruebaUltimaPosicion(fuente) {

    const b = banco({ rtt: 155, fuente });

    b.ctx.isDragging = true;

    // Movimientos rapidos que terminan en un punto conocido
    for (let i = 1; i <= 60; i++) {
        b.ctx.queueMouseMove({ x: i, y: i * 2, t: b.reloj() });
        b.saltar(8);
    }

    // Se suelta el raton: no llegan mas eventos
    await b.avanzar(b.reloj() + 300000);

    const movimientos = b.llegados.filter(l => l.accion === "move");

    return movimientos[movimientos.length - 1];
}

const ultimaPos = await pruebaUltimaPosicion(FUENTE_PROPUESTA);

comprobar(
    "al dejar de mover, la ultima posicion acaba llegando",
    ultimaPos && ultimaPos.payload.x === 60 && ultimaPos.payload.y === 120,
    ultimaPos
        ? `ultimo entregado: x=${ultimaPos.payload.x} y=${ultimaPos.payload.y}`
          + " (se esperaba x=60 y=120)"
        : "no llego ningun movimiento"
);


/* ==================================================================
   2. El orden de los botones se conserva exactamente
================================================================== */

async function pruebaOrden() {

    const b = banco({ rtt: 155, fuente: FUENTE_PROPUESTA });

    // down, mover mucho, soltar
    b.ctx.pendingMove = null;
    b.ctx.flushPendingMove();

    b.ctx.postMouse("down", { x: 0, y: 0, button: "left", t: b.reloj() });

    b.ctx.isDragging = true;

    for (let i = 1; i <= 40; i++) {
        b.ctx.queueMouseMove({ x: i, y: i, t: b.reloj() });
        b.saltar(16);
    }

    b.ctx.finishDrag({ x: 999, y: 999, t: b.reloj() });

    await b.avanzar(b.reloj() + 120000);

    return b;
}

const orden = await pruebaOrden();
const acciones = orden.llegados.map(l => l.accion);

comprobar(
    "el 'down' llega y llega el primero",
    acciones[0] === "down",
    acciones.slice(0, 4).join(" -> ")
);

comprobar(
    "el 'up' llega y llega el ultimo",
    acciones[acciones.length - 1] === "up",
    acciones.slice(-4).join(" -> ")
);

comprobar(
    "hay exactamente un 'down' y un 'up'",
    acciones.filter(a => a === "down").length === 1 &&
    acciones.filter(a => a === "up").length === 1,
    acciones.join(" ")
);

comprobar(
    "ningun movimiento se cuela despues del 'up'",
    acciones.lastIndexOf("move") < acciones.indexOf("up"),
    acciones.join(" ")
);

// El punto donde se suelta decide donde termina el arrastre
const ultimoMove = [...orden.llegados].reverse()
    .find(l => l.accion === "move");

comprobar(
    "el ultimo punto del arrastre SI se envia",
    ultimoMove && ultimoMove.payload.x === 999,
    ultimoMove ? `x=${ultimoMove.payload.x}` : "no hubo ningun move"
);

comprobar(
    "y va antes que el 'up'",
    orden.llegados.indexOf(ultimoMove) < acciones.indexOf("up"),
    acciones.join(" ")
);


/* ==================================================================
   3. Un clic suelto, sin arrastre
================================================================== */

async function pruebaClic() {

    const b = banco({ rtt: 155, fuente: FUENTE_PROPUESTA });

    b.ctx.postMouse("down", { x: 5, y: 5, button: "left", t: b.reloj() });
    b.ctx.finishDrag({ x: 5, y: 5, t: b.reloj() });

    await b.avanzar(b.reloj() + 10000);

    return b.llegados.map(l => l.accion);
}

const clic = await pruebaClic();

comprobar(
    "un clic sin mover conserva down antes de up",
    clic.indexOf("down") === 0 &&
    clic[clic.length - 1] === "up",
    clic.join(" -> ")
);


/* ==================================================================
   4. Cambiar de equipo no arrastra movimientos del anterior
================================================================== */

async function pruebaCambioDeEquipo() {

    const b = banco({ rtt: 155, fuente: FUENTE_PROPUESTA });

    b.ctx.isDragging = true;

    // Se generan movimientos para EQUIPO-A
    for (let i = 0; i < 10; i++) {
        b.ctx.queueMouseMove({ x: i, y: i, t: b.reloj() });
        b.saltar(16);
    }

    // Se cambia de equipo: es lo que hace stopCursorPolling
    b.ctx.pendingMove = null;

    if (b.ctx.moveTimer) {
        b.ctx.clearTimeout(b.ctx.moveTimer);
        b.ctx.moveTimer = null;
    }

    b.ctx.selectedDevice = { device_id: "EQUIPO-B" };

    await b.avanzar(b.reloj() + 10000);

    return b.enviados;
}

const cambio = await pruebaCambioDeEquipo();

comprobar(
    "tras cambiar de equipo no sale ningun movimiento nuevo",
    cambio.length <= 1,
    `${cambio.length} enviados en total`
);


/* ==================================================================
   5. Un fallo de red no deja el raton muerto

   Es el riesgo serio de contar cuantos movimientos van en vuelo: si
   una peticion falla y nadie descuenta, el contador se queda
   arriba para siempre y no vuelve a salir ni un movimiento mas en
   toda la sesion. Aqui se corta la red, se recupera, y se exige que
   siga funcionando.
================================================================== */

async function pruebaFalloDeRed(fuente) {

    let caida = true;
    const enviados = [];

    const ctx = {
        console,
        Date: { now: () => Date.now() },
        setTimeout, clearTimeout,

        fetch(url) {
            enviados.push(url.split("/").pop());
            return caida
                ? Promise.reject(new Error("red caida"))
                : Promise.resolve({ ok: true });
        },

        selectedDevice: { device_id: "EQUIPO-A" },
        isDragging: true,
        pressedMouseButton: "left",
        activePointerId: null,
        mouseQueue: Promise.resolve(),
        pendingMove: null,
        moveTimer: null,
        lastMouseMoveTime: 0,
        movimientosEnVuelo: 0,
    };

    createContext(ctx);

    const piezas = ["postMouse", "flushPendingMove", "queueMouseMove",
                    "finishDrag"];

    if (fuente.includes("function enviarMovimiento(")) {
        piezas.push("enviarMovimiento");
    }

    for (const nombre of piezas) {
        runInContext(extraer(fuente, nombre), ctx);
    }

    // Se silencia el console.error del catch: aqui el fallo es
    // esperado y el ruido taparia el resultado.
    const chillar = console.error;
    console.error = () => {};

    const respirar = () => new Promise(r => setTimeout(r, 30));

    for (let i = 0; i < 3; i++) {
        ctx.queueMouseMove({ x: i, y: i });
        await respirar();
    }

    const trasCaida = ctx.movimientosEnVuelo;

    caida = false;
    const antes = enviados.length;

    for (let i = 0; i < 3; i++) {
        ctx.queueMouseMove({ x: 90 + i, y: 1 });
        await respirar();
    }

    console.error = chillar;

    return {
        trasCaida,
        final: ctx.movimientosEnVuelo,
        recuperados: enviados.length - antes
    };
}

const red = await pruebaFalloDeRed(FUENTE_PROPUESTA);

comprobar(
    "una peticion fallida no deja el contador atascado",
    red.trasCaida === 0,
    `movimientos en vuelo tras tres fallos: ${red.trasCaida}`
);

comprobar(
    "y el raton vuelve a funcionar cuando vuelve la red",
    red.recuperados > 0 && red.final === 0,
    `${red.recuperados} movimientos enviados tras recuperarse`
);


/* ==================================================================
   6. El orden move -> up, mirado de cerca

   Es la unica secuencia donde resumir movimientos podria cambiar el
   RESULTADO y no solo la suavidad: si el ultimo punto llegara
   despues del 'up', el arrastre terminaria donde no es. Un archivo
   soltado en la carpeta equivocada, por ejemplo.
================================================================== */

async function pruebaMoveAntesDeUp() {

    const b = banco({ rtt: 155, fuente: FUENTE_PROPUESTA });

    b.ctx.postMouse("down", { x: 10, y: 10, button: "left", t: b.reloj() });

    b.ctx.isDragging = true;

    // Movimientos suficientes para que haya uno en vuelo al soltar
    for (let i = 1; i <= 20; i++) {
        b.ctx.queueMouseMove({ x: i, y: i, t: b.reloj() });
        b.saltar(16);
    }

    // Se suelta en un punto distinto de todos los anteriores
    b.ctx.finishDrag({ x: 777, y: 888, t: b.reloj() });

    await b.avanzar(b.reloj() + 300000);

    return b.llegados;
}

const secuencia = await pruebaMoveAntesDeUp();
const nombres = secuencia.map(l => l.accion);
const posUp = nombres.indexOf("up");

comprobar(
    "la secuencia entregada termina en 'up'",
    posUp === nombres.length - 1,
    nombres.join(" ")
);

comprobar(
    "el evento justo anterior al 'up' es un 'move'",
    posUp > 0 && nombres[posUp - 1] === "move",
    nombres.slice(Math.max(0, posUp - 3)).join(" ")
);

comprobar(
    "y ese 'move' lleva el punto exacto donde se solto",
    posUp > 0 &&
    secuencia[posUp - 1].payload.x === 777 &&
    secuencia[posUp - 1].payload.y === 888,
    posUp > 0
        ? `x=${secuencia[posUp - 1].payload.x} `
          + `y=${secuencia[posUp - 1].payload.y}`
        : "no hay nada antes del up"
);

comprobar(
    "despues del 'up' no llega absolutamente nada",
    nombres.slice(posUp + 1).length === 0,
    nombres.slice(posUp + 1).join(" ") || "(nada)"
);


/* ==================================================================
   7. Cambio de equipo con una peticion EN VUELO

   El caso que de verdad importa: unas coordenadas son de UNA
   pantalla. Si se cambia de ficha mientras el movimiento espera
   respuesta, aplicarlas en el equipo nuevo lo llevaria a un punto
   que ahi no significa nada.

   Se cambia pulsando otro equipo en la lista, que llama a
   showDeviceDetails() y NO pasa por stopCursorPolling(): por eso el
   movimiento pendiente lleva apuntado su equipo.
================================================================== */

async function pruebaCambioEnVuelo() {

    const b = banco({ rtt: 155, fuente: FUENTE_PROPUESTA });

    b.ctx.isDragging = true;

    // Primer movimiento: sale y se queda en vuelo.
    //
    // El salto previo es necesario: queueMouseMove solo envia si han
    // pasado 16 ms desde el anterior. Sin el, el primer movimiento se
    // quedaria en un temporizador y no habria nada en vuelo, que es
    // justo el escenario que esta prueba necesita montar.
    b.saltar(100);
    b.ctx.queueMouseMove({ x: 1, y: 1, t: b.reloj() });

    await new Promise(r => setImmediate(r));

    const enVueloTrasElPrimero = b.enviados.length;

    // Segundo: se queda esperando, porque hay uno en vuelo
    b.saltar(16);
    b.ctx.queueMouseMove({ x: 2, y: 2, t: b.reloj() });

    const pendienteAntes = b.ctx.pendingMove;

    // Se pulsa otro equipo en la lista: showDeviceDetails() cambia
    // selectedDevice y nada mas.
    b.ctx.selectedDevice = { device_id: "EQUIPO-B" };

    await b.avanzar(b.reloj() + 300000);

    return {
        enVueloTrasElPrimero,
        habiaPendiente: Boolean(pendienteAntes),
        enviados: b.enviados,
        quedaPendiente: b.ctx.pendingMove,
        enVuelo: b.ctx.movimientosEnVuelo
    };
}

const vuelo = await pruebaCambioEnVuelo();

comprobar(
    "el escenario es el que se queria: habia uno en vuelo y otro esperando",
    vuelo.enVueloTrasElPrimero === 1 && vuelo.habiaPendiente,
    `${vuelo.enVueloTrasElPrimero} en vuelo, pendiente: ${vuelo.habiaPendiente}`
);

comprobar(
    "el movimiento del equipo anterior NO se envia tras el cambio",
    vuelo.enviados.length === 1,
    `${vuelo.enviados.length} enviados `
    + `(${vuelo.enviados.map(e => "x=" + e.payload.x).join(", ")})`
);

comprobar(
    "y el contador queda libre, no atascado",
    vuelo.enVuelo === 0,
    `movimientos en vuelo: ${vuelo.enVuelo}`
);


/* ==================================================================
   8. Falla una peticion JUSTO antes de soltar el boton

   El peor momento para perder un paquete: si el 'up' se perdiera o
   quedara bloqueado, el equipo remoto se quedaria con el boton
   pulsado -seleccionando, arrastrando- sin que nadie lo suelte.
================================================================== */

async function pruebaFalloAlSoltar() {

    const b = banco({ rtt: 155, fuente: FUENTE_PROPUESTA });

    b.ctx.postMouse("down", { x: 3, y: 3, button: "left", t: b.reloj() });

    b.ctx.isDragging = true;

    for (let i = 1; i <= 5; i++) {
        b.ctx.queueMouseMove({ x: i, y: i, t: b.reloj() });
        b.saltar(16);
    }

    // El siguiente movimiento se pierde: es el del punto de soltado
    b.fallarLaProxima("move");

    const chillar = console.error;
    console.error = () => {};

    b.ctx.finishDrag({ x: 50, y: 50, t: b.reloj() });

    await b.avanzar(b.reloj() + 300000);

    console.error = chillar;

    return {
        acciones: b.llegados.map(l => l.accion),
        intentados: b.enviados.map(e => e.accion),
        enVuelo: b.ctx.movimientosEnVuelo
    };
}

const alSoltar = await pruebaFalloAlSoltar();

comprobar(
    "aunque falle un movimiento, el 'up' SI se envia",
    alSoltar.intentados.includes("up"),
    alSoltar.intentados.join(" ")
);

comprobar(
    "y llega: el boton no se queda pulsado en el equipo remoto",
    alSoltar.acciones.includes("up"),
    alSoltar.acciones.join(" ")
);

comprobar(
    "el fallo no deja el contador atascado",
    alSoltar.enVuelo === 0,
    `movimientos en vuelo: ${alSoltar.enVuelo}`
);


/* ==================================================================
   9. Perder el foco en mitad de un arrastre

   finishDrag solo se llamaba desde onPointerUp y onPointerCancel.
   Si la ventana pierde el foco mientras arrastras -Alt+Tab, cambiar
   de pestaña, el equipo que se bloquea- el 'pointerup' puede ocurrir
   donde el navegador no lo ve y no llegar nunca.

   Con la sesion todavia viva, ni el panel ni el Agent sueltan el
   boton: se queda PULSADO en el equipo de la persona.
================================================================== */

function bancoDeFoco() {

    const b = banco({ rtt: 20, fuente: FUENTE_PROPUESTA });

    const cuerpo = extraer(FUENTE_PROPUESTA, "soltarArrastrePorFoco");

    if (!cuerpo) return null;

    runInContext(cuerpo, b.ctx);

    return b;
}

const conFoco = bancoDeFoco();

comprobar(
    "existe soltarArrastrePorFoco",
    conFoco !== null,
    conFoco ? "" : "no se encontro la funcion en app.js"
);

if (conFoco) {

    // --- arrastrando: tiene que soltar ---
    conFoco.ctx.isDragging = true;
    conFoco.ctx.activePointerId = 1;
    conFoco.ctx.pressedMouseButton = "left";

    conFoco.ctx.postMouse("down", { x: 5, y: 5, button: "left" });

    conFoco.ctx.soltarArrastrePorFoco();

    await conFoco.avanzar(conFoco.reloj() + 10000);

    const acciones = conFoco.llegados.map(l => l.accion);

    comprobar(
        "al perder el foco arrastrando, se envia el 'up'",
        acciones.includes("up"),
        acciones.join(" ") || "(nada)"
    );

    comprobar(
        "y el 'up' va despues del 'down'",
        acciones.indexOf("down") < acciones.lastIndexOf("up"),
        acciones.join(" ")
    );

    comprobar(
        "el arrastre queda cerrado",
        conFoco.ctx.isDragging === false,
        `isDragging = ${conFoco.ctx.isDragging}`
    );

    // --- un movimiento PENDIENTE sale antes del up ---
    //
    // Es lo que podria romper el coalescing de d1a295a: si al
    // perder el foco el ultimo punto se quedara en el buffer, el
    // arrastre terminaria donde no es.
    const conPendiente = bancoDeFoco();

    conPendiente.ctx.isDragging = true;
    conPendiente.ctx.pressedMouseButton = "left";

    conPendiente.ctx.postMouse("down", { x: 1, y: 1, button: "left" });

    // Se deja uno pendiente simulando que hay otro en vuelo
    conPendiente.ctx.movimientosEnVuelo = 1;
    conPendiente.ctx.queueMouseMove({ x: 555, y: 666 });
    conPendiente.ctx.movimientosEnVuelo = 0;

    const habiaPendiente = Boolean(conPendiente.ctx.pendingMove);

    conPendiente.ctx.soltarArrastrePorFoco();

    await conPendiente.avanzar(conPendiente.reloj() + 30000);

    const sec = conPendiente.llegados.map(l => l.accion);
    const movs = conPendiente.llegados.filter(l => l.accion === "move");

    comprobar(
        "habia un movimiento pendiente al perder el foco",
        habiaPendiente
    );

    comprobar(
        "el movimiento pendiente SI se envia",
        movs.some(m => m.payload.x === 555),
        sec.join(" ")
    );

    comprobar(
        "y va antes del 'up' (no rompe el coalescing)",
        sec.indexOf("move") >= 0 && sec.indexOf("move") < sec.indexOf("up"),
        sec.join(" ")
    );

    comprobar(
        "el contador de movimientos queda libre",
        conPendiente.ctx.movimientosEnVuelo === 0,
        String(conPendiente.ctx.movimientosEnVuelo)
    );

    // --- SIN arrastrar: no debe enviar nada ---
    //
    // Es el riesgo del arreglo: soltar botones por una perdida de
    // foco irrelevante molestaria mas que el fallo que corrige.
    const sinArrastre = bancoDeFoco();

    sinArrastre.ctx.isDragging = false;

    sinArrastre.ctx.soltarArrastrePorFoco();

    await sinArrastre.avanzar(sinArrastre.reloj() + 10000);

    comprobar(
        "sin arrastre, perder el foco NO envia nada",
        sinArrastre.enviados.length === 0,
        `${sinArrastre.enviados.length} enviados`
    );

    // --- repetido: idempotente ---
    const repetido = bancoDeFoco();

    repetido.ctx.isDragging = true;
    repetido.ctx.pressedMouseButton = "left";

    repetido.ctx.soltarArrastrePorFoco();
    repetido.ctx.soltarArrastrePorFoco();
    repetido.ctx.soltarArrastrePorFoco();

    await repetido.avanzar(repetido.reloj() + 10000);

    comprobar(
        "perder el foco tres veces solo suelta una",
        repetido.enviados.filter(e => e.accion === "up").length === 1,
        `${repetido.enviados.filter(e => e.accion === "up").length} 'up' enviados`
    );
}


/* ==================================================================
   10. Que este realmente enganchado, y al evento correcto
================================================================== */

comprobar(
    "se escucha el blur de la VENTANA",
    /window\.addEventListener\(\s*\n?\s*"blur",\s*\n?\s*soltarArrastrePorFoco/
        .test(FUENTE_PROPUESTA),
    "window.addEventListener(\"blur\", soltarArrastrePorFoco)"
);

comprobar(
    "y el visibilitychange, para el cambio de pestaña",
    /document\.hidden\)\s*\{\s*\n?\s*soltarArrastrePorFoco\(\)/
        .test(FUENTE_PROPUESTA),
    "document.hidden -> soltarArrastrePorFoco()"
);

// El marco pierde el foco al pulsar un boton de la barra. Enganchar
// ahi cortaria arrastres validos.
comprobar(
    "NO se engancha al blur del marco",
    !/marco\.addEventListener\("blur",\s*soltarArrastrePorFoco/
        .test(FUENTE_PROPUESTA),
    "el blur del marco sigue siendo solo del teclado"
);

comprobar(
    "lostpointercapture sigue cerrando el arrastre",
    /"lostpointercapture",\s*\n?\s*onPointerCancel/.test(FUENTE_PROPUESTA)
);


/* ------------------------------------------------------------------ */

console.log("");
console.log("=".repeat(66));
console.log("LA COLA DEL MOUSE REMOTO CON LATENCIA");
console.log("=".repeat(66));
console.log("");
console.log(`  archivo   : ${RUTA}`);
console.log(`  referencia: ${RUTA_REFERENCIA || "(ninguna)"}`);
console.log("");

let fallos = 0;

for (const [nombre, bien, detalle] of resultados) {
    console.log(`  ${bien ? "OK   " : "FALLA"}  ${nombre}`);
    if (!bien) {
        fallos++;
        if (detalle) console.log(`          ${detalle}`);
    } else if (detalle) {
        console.log(`          ${detalle}`);
    }
}

console.log("");
console.log(`${resultados.length - fallos}/${resultados.length} comprobaciones`);

process.exit(fallos ? 1 : 0);
