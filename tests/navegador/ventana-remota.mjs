/*
Prueba la ventana de control remoto en Chrome de verdad.

Minimiza, restaura, redimensiona varias veces y cierra, comprobando
en cada paso lo que importa: que la imagen conserva su proporcion,
que la barra de teclas sigue alcanzable, y —lo decisivo— que el
elemento de la transmision NO se destruye ni se recarga al plegar.

Sin dependencias: se habla con el protocolo de depuracion de Chrome
por el WebSocket que trae Node.
*/

// Los puertos los decide quien lanza la sonda, para no chocar con
// un servidor o un Chrome que ya esten corriendo.
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
    width: 1366, height: 900, deviceScaleFactor: 1, mobile: false
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

// Se destapa la ventana remota sin iniciar sesion ni red: solo
// interesa el comportamiento del maquetado.
const PREPARAR = `(() => {
    document.getElementById("login-screen")?.classList.add("hidden");
    document.getElementById("app-shell")?.classList.remove("hidden");

    const caja = document.getElementById("remote-screen");
    caja.classList.remove("hidden");

    // La ventana remota vive dentro de la ficha del equipo, que a su
    // vez esta oculta hasta que se selecciona uno. Se destapan todos
    // sus antepasados: aqui solo interesa el maquetado.
    let nodo = caja.parentElement;

    while (nodo && nodo !== document.body) {
        nodo.classList.remove("hidden");
        nodo = nodo.parentElement;
    }

    const img = document.getElementById("remote-screen-image");

    // Un PNG de 4x3 para que haya proporcion que conservar
    img.src = "data:image/svg+xml;base64," + btoa(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
        + '<rect width="1280" height="720" fill="#246"/></svg>'
    );

    return true;
})()`;

const MEDIR = `(() => {
    const marco = document.getElementById("remote-screen-container");
    const img = document.getElementById("remote-screen-image");
    const cuerpo = document.getElementById("remote-cuerpo");
    const teclas = document.getElementById("remote-keys");
    const boton = document.getElementById("remote-minimizar");
    const cerrar = document.getElementById("close-remote-button");

    const rm = marco.getBoundingClientRect();
    const ri = img.getBoundingClientRect();
    const rc = cuerpo.getBoundingClientRect();
    const rt = teclas.getBoundingClientRect();
    const rb = boton.getBoundingClientRect();
    const rx = cerrar.getBoundingClientRect();

    // Cuanto del area del marco ocupa la imagen DIBUJADA, con su
    // proporcion: es lo que delata una deformacion.
    const escala = Math.min(ri.width / img.naturalWidth,
                            ri.height / img.naturalHeight);

    return {
        marco: [Math.round(rm.width), Math.round(rm.height)],
        imagen: [Math.round(ri.width), Math.round(ri.height)],
        dibujada: [Math.round(img.naturalWidth * escala),
                   Math.round(img.naturalHeight * escala)],
        natural: [img.naturalWidth, img.naturalHeight],
        cuerpoAlto: Math.round(rc.height),
        teclasVisibles: rt.width > 0 && rt.height > 0,
        botonVisible: rb.width > 0 && rb.height > 0,
        cerrarVisible: rx.width > 0 && rx.height > 0,
        imagenEnElDom: Boolean(img.isConnected),
        src: img.getAttribute("src") ? "puesto" : "sin src",
        display: getComputedStyle(cuerpo).display
    };
})()`;

const informe = [];
function anotar(paso, datos) { informe.push({ paso, ...datos }); }

await evaluar(PREPARAR);
await new Promise(r => setTimeout(r, 400));

// Identidad del elemento de video, para saber si se recrea
await evaluar(`window.__img = document.getElementById("remote-screen-image");
               window.__recargas = 0;
               window.__img.addEventListener("load", () => window.__recargas++);
               true`);

anotar("abierta", await evaluar(MEDIR));

// --- minimizar y restaurar, tres veces ---
for (let i = 1; i <= 3; i++) {

    await evaluar("plegarRemoto(true)");
    await new Promise(r => setTimeout(r, 300));
    anotar(`plegada ${i}`, await evaluar(MEDIR));

    await evaluar("plegarRemoto(false)");
    await new Promise(r => setTimeout(r, 300));
    anotar(`restaurada ${i}`, await evaluar(MEDIR));
}

// --- redimensionar, varias veces ---
for (const [w, h] of [[640, 360], [1100, 620], [320, 200], [900, 500]]) {

    await evaluar(`(() => {
        const m = document.getElementById("remote-screen-container");
        m.style.width = "${w}px";
        m.style.height = "${h}px";
        return true;
    })()`);

    await new Promise(r => setTimeout(r, 250));
    anotar(`tamano ${w}x${h}`, await evaluar(MEDIR));
}

// --- lo que importa al final ---
const final = await evaluar(`({
    mismoElemento: window.__img === document.getElementById("remote-screen-image"),
    recargas: window.__recargas,
    imagenes: document.querySelectorAll("#remote-screen-image").length,
    marcos: document.querySelectorAll("#remote-screen-container").length
})`);

console.log(JSON.stringify({ pasos: informe, final }, null, 1));
process.exit(0);
