import { RELAY_URL } from "./config.js";
// justin.md R5: pairing from the URL fragment, tweetnacl keypair in localStorage, GET /v0/inbox every 5 s with the bearer,
// decrypt and render Clinical Signal Card v0 (relay/README.md). Never renders a card it cannot decrypt and authenticate.
document.getElementById("out").textContent = `RELAY_URL=${RELAY_URL}\n(placeholder: mock Ascend)`;
