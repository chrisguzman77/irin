import { CLOUD_URL, FAMILY_BEARER } from "./config.js";
// justin.md step 9 / A3: GET /v1/family/last_night with the family bearer, refresh every 60 s, stale after 15 min.
document.getElementById("out").textContent = `CLOUD_URL=${CLOUD_URL}\nbearer=${FAMILY_BEARER ? "set" : "missing"}\n(placeholder)`;
