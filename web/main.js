document.getElementById("user-input").addEventListener("keyup", function(event) {
    if (event.key === "Enter") {
        sendMessage();
    }
});

async function sendMessage() {
    const inputField = document.getElementById("user-input");
    const query = inputField.value.trim();
    if (!query) return;

    const chatDisplay = document.getElementById("chat-display");
    const coreOrb = document.getElementById("core-orb");

    chatDisplay.innerHTML += `<div class="user-msg">USER_DECK// ${query}</div>`;
    inputField.value = "";
    chatDisplay.scrollTop = chatDisplay.scrollHeight;

    coreOrb.className = "orb thinking";

    try {
        const response = await fetch("http://localhost:5000/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: query })
        });
        const data = await response.json();
        const reply = data.reply;

        chatDisplay.innerHTML += `<div class="jarvis-msg">JARVIS_CORE// ${reply}</div>`;
        chatDisplay.scrollTop = chatDisplay.scrollHeight;
        coreOrb.className = "orb idle";

    } catch (error) {
        chatDisplay.innerHTML += `<div class="jarvis-msg">JARVIS_CORE// connection error — is the backend running?? 😭</div>`;
        coreOrb.className = "orb idle";
    }
}

setInterval(async () => {
    try {
        const res = await fetch("http://localhost:5000/yap_poll");
        const data = await res.json();
        if (data.message) {
            const chatDisplay = document.getElementById("chat-display");
            chatDisplay.innerHTML += `<div class="jarvis-msg">JARVIS_CORE// ${data.message}</div>`;
            chatDisplay.scrollTop = chatDisplay.scrollHeight;
        }
    } catch (error) {}
}, 2000);