// Names for the generated contract types (src/types/contracts.d.ts, from
// backend/app/contracts.py via `npm run types:contracts`). Aliases only:
// the app never hand-declares a message shape.
import type { components } from "../types/contracts";

type S = components["schemas"];
export type StateSnapshot = S["StateSnapshot"];
export type WSMessage = S["WSMessage"];
export type WSMessageType = WSMessage["type"];
export type Reading = S["Reading"];
export type Forecast = S["Forecast"];
export type AlarmState = S["AlarmState"];
export type Settings = S["Settings"];
export type PresenceState = S["PresenceState"];
