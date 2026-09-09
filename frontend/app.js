async function checkServer() {
    const statusElement = document.getElementById("server-status");

    try {
        const response = await fetch("/api/health");

        if (!response.ok) {
            throw new Error("Server error");
        }

        statusElement.textContent = "Servidor online";

    } catch (error) {

        statusElement.textContent = "Servidor offline";
    }
}


async function loadDevices() {

    const table = document.getElementById("devices-table");

    try {

        const response = await fetch("/api/devices");

        if (!response.ok) {
            throw new Error("Error loading devices");
        }

        const devices = await response.json();

        updateCounters(devices);

        table.innerHTML = "";

        if (devices.length === 0) {

            table.innerHTML = `
                <tr>
                    <td colspan="5">
                        No hay dispositivos registrados
                    </td>
                </tr>
            `;

            return;
        }


        devices.forEach(device => {

            const row = document.createElement("tr");

            const statusClass =
                device.status === "online"
                    ? "online"
                    : "offline";

            row.innerHTML = `
                <td>${device.hostname}</td>

                <td>${device.operating_system || "-"}</td>

                <td>${device.ip_address || "-"}</td>

                <td class="${statusClass}">
                    ${device.status}
                </td>

                <td>
                    ${formatDate(device.last_seen)}
                </td>
            `;

            table.appendChild(row);
        });


    } catch (error) {

        table.innerHTML = `
            <tr>
                <td colspan="5">
                    Error al cargar dispositivos
                </td>
            </tr>
        `;
    }
}


function updateCounters(devices) {

    const total = devices.length;

    const online =
        devices.filter(
            device => device.status === "online"
        ).length;

    const offline = total - online;

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

    const date = new Date(dateString);

    return date.toLocaleString("es-MX");
}


async function refreshDashboard() {

    await checkServer();
    await loadDevices();
}


refreshDashboard();

setInterval(refreshDashboard, 10000);