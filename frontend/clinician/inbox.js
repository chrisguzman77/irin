import { RELAY_URL } from "./config.js";
import { renderCard } from "./card.js";
// justin.md R5: pairing from the URL fragment, tweetnacl keypair in localStorage, GET /v0/inbox every 5 s with the bearer,
// decrypt and render Clinical Signal Card v0 (relay/README.md). Never renders a card it cannot decrypt and authenticate.
// Until the relay exists (R6) the page previews the two sample cards (backend/tests/fixtures, copied to fixtures/).
void RELAY_URL;

const FIXTURES = { standing: "fixtures/signal_card_standing.json", step: "fixtures/signal_card_step.json" };
const box = document.getElementById("card");

async function show(name) {
  for (const b of document.querySelectorAll("[data-fixture]")) b.setAttribute("aria-pressed", String(b.dataset.fixture === name));
  try {
    const res = await fetch(FIXTURES[name], { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    box.replaceChildren(renderCard(await res.json(), { actions: true, sample: true }));
  } catch (e) {
    box.replaceChildren();
    const out = document.getElementById("out");
    out.hidden = false;
    out.textContent = `could not load the sample card (${e.message}); serve this folder over http`;
  }
}

for (const b of document.querySelectorAll("[data-fixture]")) b.addEventListener("click", () => show(b.dataset.fixture));
show(new URLSearchParams(location.search).get("sample") === "step" ? "step" : "standing");
