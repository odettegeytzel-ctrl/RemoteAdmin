
let selectedDevice = null;


async function checkServer() {

    const statusElement =
        document.getElementById("server-status");

    try {

        const response =
            await fetch("/api/health");

        if (!response.ok) {
            throw new Error("Server error");
        }

        statusElement.textContent =
            "Servidor online";

        statusElement.className =
            "px-4 py-2 rounded-full bg-emerald-50 text-emerald-700 text-sm";

    } catch (error) {

        statusElement.textContent =
            "Servidor offline";

        statusElement.className =
            "px-4 py-2 rounded-full bg-red-50 text-red-700 text-sm";
    }
}


async function loadDevices() {

    const table =
        document.getElementById("devices-table");

    try {

        const response =
            await fetch("/api/devices");

        if (!response.ok) {
            throw new Error("Error loading devices");
        }

        const devices =
            await response.json();

        updateCounters(devices);

        table.innerHTML = "";

        if (devices.length === 0) {

            table.innerHTML = `
                <tr>
                    <td
                        colspan="5"
                        class="px-6 py-10 text-center text-slate-500"
                    >
                        No hay dispositivos registrados
                    </td>
                </tr>
            `;

            return;
        }


        devices.forEach(device => {

            const row =
                document.createElement("tr");

            row.className =
                "hover:bg-slate-50 cursor-pointer transition";


            const statusOnline =
                device.status === "online";


            const statusBadge =
                statusOnline
                    ? `
                        <span class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-50 text-emerald-700 text-xs font-medium">
                            <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
                            Online
                        </span>
                    `
                    : `
                        <span class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-red-50 text-red-700 text-xs font-medium">
                            <span class="w-2 h-2 rounded-full bg-red-500"></span>
                            Offline
                        </span>
                    `;


            row.innerHTML = `

                <td class="px-6 py-4">

                    <div>

                        <p class="font-medium text-slate-900">
                            ${device.hostname}
                        </p>

                        <p class="text-xs text-slate-500 mt-1">
                            ${device.device_id}
                        </p>

                    </div>

                </td>


                <td class="px-6 py-4 text-slate-600">
                    ${device.operating_system || "-"}
                </td>


                <td class="px-6 py-4 text-slate-600">
                    ${device.ip_address || "-"}
                </td>


                <td class="px-6 py-4">
                    ${statusBadge}
                </td>


                <td class="px-6 py-4 text-slate-500">
                    ${formatDate(device.last_seen)}
                </td>

            `;


            row.addEventListener(
                "click",
                () => showDeviceDetails(device)
            );


            table.appendChild(row);

        });


    } catch (error) {

        table.innerHTML = `

            <tr>

                <td
                    colspan="5"
                    class="px-6 py-10 text-center text-red-500"
                >
                    Error al cargar dispositivos
                </td>

            </tr>

        `;
    }
}


function updateCounters(devices) {

    const total =
        devices.length;


    const online =
        devices.filter(
            device => device.status === "online"
        ).length;


    const offline =
        total - online;


    document.getElementById(
        "total-devices"
    ).textContent = total;


    document.getElementById(
        "online-devices"
    ).textContent = online;


    document.getElementById(
        "offline-devices"
    ).textContent = offline;
}


function formatDate(dateString) {

    if (!dateString) {
        return "-";
    }


    const date =
        new Date(dateString);


    return date.toLocaleString(
        "es-MX"
    );
}


function showDeviceDetails(device) {
    selectedDevice = device;

    document.getElementById("detail-hostname").textContent =
        device.hostname || "Sin información";

    document.getElementById("detail-username").textContent =
        device.username || "Sin información";

    document.getElementById("detail-os").textContent =
        device.operating_system || "Sin información";

    document.getElementById("detail-ip").textContent =
        device.ip_address || "Sin información";

    document.getElementById("detail-processor").textContent =
        device.processor || "Sin información";

    document.getElementById("detail-cpu").textContent =
        device.cpu_count !== null && device.cpu_count !== undefined
            ? `${device.cpu_count} núcleos`
            : "Sin información";

    document.getElementById("detail-status").textContent =
        device.status || "Sin información";

    document.getElementById("detail-last-seen").textContent =
        formatDate(device.last_seen);

    document.getElementById("device-details").classList.remove("hidden");

    updateActionButtons();
}



function updateActionButtons() {

    const pingButton =
        document.getElementById(
            "ping-device-button"
        );


    const systemInfoButton =
        document.getElementById(
            "system-info-button"
        );


    if (!selectedDevice) {

        pingButton.disabled = true;
        systemInfoButton.disabled = true;

        return;
    }


    const isOnline =
        selectedDevice.status === "online";


    pingButton.disabled =
        !isOnline;


    systemInfoButton.disabled =
        !isOnline;


    if (isOnline) {

        pingButton.className =
            "px-4 py-2 rounded-lg bg-slate-900 text-white text-sm hover:bg-slate-700 transition";


        systemInfoButton.className =
            "px-4 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-500 transition";

    } else {

        pingButton.className =
            "px-4 py-2 rounded-lg bg-slate-200 text-slate-400 text-sm cursor-not-allowed";


        systemInfoButton.className =
            "px-4 py-2 rounded-lg bg-slate-200 text-slate-400 text-sm cursor-not-allowed";
    }
}



async function pingDevice() {

    if (!selectedDevice) {
        return;
    }

    const deviceId =
        selectedDevice.device_id;

    const statusElement =
        document.getElementById("action-status");

    statusElement.textContent =
        "Comprobando conexión...";

    statusElement.className =
        "px-4 py-3 rounded-lg text-sm bg-slate-100 text-slate-600";

    try {

        const response =
            await fetch(
                `/api/devices/${deviceId}/ping`,
                {
                    method: "POST"
                }
            );

        const result =
            await response.json();

        if (result.status === "sent") {

            statusElement.textContent =
                `Ping enviado correctamente a ${selectedDevice.hostname}`;

            statusElement.className =
                "px-4 py-3 rounded-lg text-sm bg-emerald-50 text-emerald-700";

        } else {

            statusElement.textContent =
                result.message ||
                "No fue posible enviar el ping";

            statusElement.className =
                "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
        }

    } catch (error) {

        statusElement.textContent =
            "Error al comunicarse con el servidor";

        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
    }
}



async function requestSystemInfo() {

    if (!selectedDevice) {
        return;
    }

    const deviceId =
        selectedDevice.device_id;

    const statusElement =
        document.getElementById("action-status");

    statusElement.textContent =
        "Solicitando información del equipo...";

    statusElement.className =
        "px-4 py-3 rounded-lg text-sm bg-slate-100 text-slate-600";

    try {

        const response =
            await fetch(
                `/api/devices/${deviceId}/system-info`,
                {
                    method: "POST"
                }
            );

        const result =
            await response.json();

        if (result.status !== "sent") {

            statusElement.textContent =
                result.message ||
                "No fue posible solicitar la información";

            statusElement.className =
                "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";

            return;
        }

        statusElement.textContent =
            "Esperando respuesta del equipo...";

        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-blue-50 text-blue-700";


        let attempts = 0;

        const maxAttempts = 10;


        const checkInformation = async () => {

            attempts++;

            try {

                const devicesResponse =
                    await fetch("/api/devices");

                const devices =
                    await devicesResponse.json();

                const updatedDevice =
                    devices.find(
                        device =>
                            device.device_id === deviceId
                    );


                if (
                    updatedDevice &&
                    updatedDevice.username &&
                    updatedDevice.processor &&
                    updatedDevice.cpu_count !== null
                ) {

                    selectedDevice =
                        updatedDevice;

                    showDeviceDetails(
                        updatedDevice
                    );

                    statusElement.textContent =
                        "Información del equipo actualizada.";

                    statusElement.className =
                        "px-4 py-3 rounded-lg text-sm bg-emerald-50 text-emerald-700";

                    return;
                }


                if (attempts < maxAttempts) {

                    setTimeout(
                        checkInformation,
                        1000
                    );

                    return;
                }


                statusElement.textContent =
                    "El equipo no respondió a tiempo.";

                statusElement.className =
                    "px-4 py-3 rounded-lg text-sm bg-amber-50 text-amber-700";

            } catch (error) {

                statusElement.textContent =
                    "Error al consultar la información del equipo.";

                statusElement.className =
                    "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
            }
        };


        setTimeout(
            checkInformation,
            1000
        );

    } catch (error) {

        statusElement.textContent =
            "Error al comunicarse con el servidor";

        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
    }
}





document
    .getElementById("ping-device-button")
    .addEventListener(
        "click",
        pingDevice
    );


document
    .getElementById("system-info-button")
    .addEventListener(
        "click",
        requestSystemInfo
    );


async function refreshDashboard() {

    await checkServer();

    await loadDevices();

    if (selectedDevice) {

        const currentDevice =
            document.querySelector(
                `[data-device-id="${selectedDevice.device_id}"]`
            );
    }
}


refreshDashboard();


setInterval(
    refreshDashboard,
    10000
);

