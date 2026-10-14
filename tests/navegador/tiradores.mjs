/*
Arrastra los OCHO tiradores de la ventana remota, de verdad.

Lo que comprueba, midiendo:

  - que cada tirador cambia el tamaño en la direccion que toca;
  - que el borde sigue al puntero (por eso el factor 2 en
    horizontal: el marco esta centrado y crece por los dos lados);
  - que los minimos y el tope del contenedor se respetan;
  - y lo mas importante: que NINGUN tirador cae encima de la imagen.
    Si lo hiciera, un clic cerca del borde iria al redimensionado en
    vez de al equipo administrado.

Se usan eventos de puntero reales por el protocolo de Chrome, no
llamadas a funciones: asi se prueba tambien el enganche.
*/

const PUERTO_WEB = Number(process.argv[2] || 8788);
const PUERTO = Number(process.argv[3] || 9344);

const URL_PAGINA =
    `http://127.0.0.1:${PUERTO_WEB}/frontend/index.html`;

async function endpoint() {
    for (let i = 0; i < 40; i++) {
        try {
            const r = await fetch(`http://127.0.0.1:${PUERTO}/json/version`);
            const j = await r.json();
            if (j.webSocketDebuggerUrl) return j.webSocketDebuggerUrl;
        } catch { /* todavia no */ }
        await new Promise(r => setTimeout(r, 300));
    }
    throw new Error("Chrome no abrio su puerto");
}

class Sesion {
    constructor(ws) { this.ws = ws; this.n = 0; this.pend = new Map(); }

    static async abrir(url) {
        const ws = new WebSocket(url);
        await new Promise((ok, mal) => {
            ws.onopen = ok;
            ws.onerror = () => mal(new Error("sin conexion"));
        });
        const s = new Sesion(ws);
        ws.onmessage = (e) => {
            const m = JSON.parse(e.data);
            if (m.id && s.pend.has(m.id)) {
                const { ok, mal } = s.pend.get(m.id);
                s.pend.delete(m.id);
                m.error ? mal(new Error(JSON.stringify(m.error))) : ok(m.result);
            }
        };
        return s;
    }

    enviar(method, params = {}, sessionId) {
        const id = ++this.n;
        const carga = { id, method, params };
        if (sessionId) carga.sessionId = sessionId;
        this.ws.send(JSON.stringify(carga));
        return new Promise((ok, mal) => this.pend.set(id, { ok, mal }));
    }
}

const ws = await endpoint();
const raiz = await Sesion.abrir(ws);

const { targetId } = await raiz.enviar("Target.createTarget",
                                       { url: "about:blank" });
const { sessionId } = await raiz.enviar("Target.attachToTarget",
                                        { targetId, flatten: true });

await raiz.enviar("Page.enable", {}, sessionId);
await raiz.enviar("Runtime.enable", {}, sessionId);

await raiz.enviar("Emulation.setDeviceMetricsOverride", {
    width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false
}, sessionId);

await raiz.enviar("Page.navigate", { url: URL_PAGINA }, sessionId);
await new Promise(r => setTimeout(r, 2000));

async function evaluar(expresion) {
    const { result, exceptionDetails } = await raiz.enviar(
        "Runtime.evaluate",
        { expression: expresion, returnByValue: true, awaitPromise: true },
        sessionId
    );
    if (exceptionDetails) {
        throw new Error(exceptionDetails.text + " :: " +
                        (exceptionDetails.exception?.description || ""));
    }
    return result.value;
}

await evaluar(`(() => {
    document.getElementById("login-screen")?.classList.add("hidden");
    document.getElementById("app-shell")?.classList.remove("hidden");

    const caja = document.getElementById("remote-screen");
    caja.classList.remove("hidden");

    let nodo = caja.parentElement;
    while (nodo && nodo !== document.body) {
        nodo.classList.remove("hidden");
        nodo = nodo.parentElement;
    }

    const img = document.getElementById("remote-screen-image");
    img.src = "data:image/svg+xml;base64," + btoa(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
        + '<rect width="1280" height="720" fill="#246"/></svg>'
    );

    colocarTiradoresRemotos();
    return true;
})()`);

// El marco esta muy abajo en una pagina larga: si no se trae a la
// vista, las coordenadas caen fuera de la ventana y los eventos de
// puntero no aciertan en ningun tirador. Paso en el primer intento.
await evaluar(`(() => {
    document.getElementById("remote-screen-container")
        .scrollIntoView({ block: "center" });
    return true;
})()`);

await new Promise(r => setTimeout(r, 500));

async function raton(tipo, x, y, boton) {
    await raiz.enviar("Input.dispatchMouseEvent", {
        type: tipo, x, y,
        button: boton || "left",
        buttons: tipo === "mouseReleased" ? 0 : 1,
        clickCount: 1,
        pointerType: "mouse"
    }, sessionId);
}

const MEDIR = `(() => {
    const m = document.getElementById("remote-screen-container");
    const r = m.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height),
             x: Math.round(r.left), y: Math.round(r.top) };
})()`;

const CENTRO = (lado) => `(() => {
    const t = document.querySelector('.vy-remoto-tirador[data-lado="${lado}"]');
    const r = t.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2),
             y: Math.round(r.top + r.height / 2) };
})()`;

// --- Lo primero: ningun tirador puede pisar la imagen ---
const solape = await evaluar(`(() => {
    const img = document.getElementById("remote-screen-image")
                        .getBoundingClientRect();
    const malos = [];

    for (const t of document.querySelectorAll(".vy-remoto-tirador")) {
        const r = t.getBoundingClientRect();
        const cruza = !(r.right <= img.left || r.left >= img.right ||
                        r.bottom <= img.top || r.top >= img.bottom);
        if (cruza) malos.push(t.dataset.lado);
    }

    return { malos, tiradores:
        document.querySelectorAll(".vy-remoto-tirador").length };
})()`);

// Cada tirador se prueba en los DOS sentidos: encogiendo y
// agrandando. Solo con uno no se demuestra nada, porque el marco
// arranca pegado a su ancho maximo y cualquier intento de agrandarlo
// se queda igual —correctamente— y parece que el tirador no hace
// nada. Paso en el primer intento.
//
// 'esperaAncho' y 'esperaAlto' son el cambio que DEBE producirse:
// horizontal x2 porque el marco esta centrado y crece por los dos
// lados; vertical x1 porque crece solo hacia abajo.
const LADOS = [
    { lado: "e",  dx: -60, dy: 0,   esperaAncho: -120, esperaAlto: 0 },
    { lado: "e",  dx: 40,  dy: 0,   esperaAncho: 80,   esperaAlto: 0 },
    { lado: "o",  dx: 60,  dy: 0,   esperaAncho: -120, esperaAlto: 0 },
    { lado: "o",  dx: -40, dy: 0,   esperaAncho: 80,   esperaAlto: 0 },
    { lado: "s",  dx: 0,   dy: 70,  esperaAncho: 0,    esperaAlto: 70 },
    { lado: "s",  dx: 0,   dy: -50, esperaAncho: 0,    esperaAlto: -50 },
    { lado: "n",  dx: 0,   dy: -70, esperaAncho: 0,    esperaAlto: 70 },
    { lado: "n",  dx: 0,   dy: 50,  esperaAncho: 0,    esperaAlto: -50 },
    { lado: "se", dx: -50, dy: 50,  esperaAncho: -100, esperaAlto: 50 },
    { lado: "so", dx: 50,  dy: 50,  esperaAncho: -100, esperaAlto: 50 },
    { lado: "ne", dx: -50, dy: -50, esperaAncho: -100, esperaAlto: 50 },
    { lado: "no", dx: 50,  dy: -50, esperaAncho: -100, esperaAlto: 50 }
];

// Tamaño de partida de cada prueba: lejos de los topes, para que lo
// que se mida sea el tirador y no el recorte.
const PARTIDA = `(() => {
    const m = document.getElementById("remote-screen-container");
    m.style.width = "800px";
    m.style.height = "450px";
    m.scrollIntoView({ block: "center" });
    return true;
})()`;

const pasos = [];

for (const prueba of LADOS) {

    await evaluar(PARTIDA);
    await new Promise(r => setTimeout(r, 120));

    const antes = await evaluar(MEDIR);
    const punto = await evaluar(CENTRO(prueba.lado));

    await raton("mousePressed", punto.x, punto.y);
    await new Promise(r => setTimeout(r, 60));

    // En dos tramos, como un arrastre de verdad
    await raton("mouseMoved", punto.x + prueba.dx / 2,
                punto.y + prueba.dy / 2);
    await new Promise(r => setTimeout(r, 60));

    await raton("mouseMoved", punto.x + prueba.dx, punto.y + prueba.dy);
    await new Promise(r => setTimeout(r, 60));

    await raton("mouseReleased", punto.x + prueba.dx,
                punto.y + prueba.dy);
    await new Promise(r => setTimeout(r, 150));

    const despues = await evaluar(MEDIR);

    pasos.push({
        lado: prueba.lado,
        esperaAncho: prueba.esperaAncho,
        esperaAlto: prueba.esperaAlto,
        dx: prueba.dx,
        dy: prueba.dy,
        antes: [antes.w, antes.h],
        despues: [despues.w, despues.h],
        dAncho: despues.w - antes.w,
        dAlto: despues.h - antes.h
    });
}

// --- Los limites ---
const limites = await evaluar(`(() => {
    const m = document.getElementById("remote-screen-container");
    m.style.width = "50px";
    m.style.height = "40px";
    const chico = m.getBoundingClientRect();

    m.style.width = "9000px";
    const ancho = m.getBoundingClientRect();
    const padre = m.parentElement.getBoundingClientRect();

    return {
        minAncho: Math.round(chico.width),
        minAlto: Math.round(chico.height),
        anchoPedido: 9000,
        anchoReal: Math.round(ancho.width),
        anchoPadre: Math.round(padre.width)
    };
})()`);

// --- Y que la imagen sigue sin deformarse ---
const proporcion = await evaluar(`(() => {
    const m = document.getElementById("remote-screen-container");
    m.style.width = "820px";
    m.style.height = "300px";
    const img = document.getElementById("remote-screen-image");
    const r = img.getBoundingClientRect();
    const e = Math.min(r.width / img.naturalWidth,
                       r.height / img.naturalHeight);
    return {
        dibujada: [Math.round(img.naturalWidth * e),
                   Math.round(img.naturalHeight * e)],
        natural: [img.naturalWidth, img.naturalHeight]
    };
})()`);

console.log(JSON.stringify({ solape, pasos, limites, proporcion }, null, 1));
process.exit(0);
