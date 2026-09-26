// Types for cardModel.js, a byte-for-byte copy of frontend/clinician/card-model.js
// (one card model for both renderers). The card input is the generated SignalCard.
import type { SignalCard } from "./contracts";

export interface CardRow {
  key: string;
  label: string;
  /** null = the card carries no value: rendered as "no data", never blank */
  value: string | null;
  /** null = the metric has no confidence label on the card: rendered as "no label" */
  conf: "measured" | "reported" | "inferred" | null;
}
export interface CardNight {
  date: string;
  text: string;
  cls: string;
  source: "logged" | "inferred" | "unknown";
  title: string;
}
export interface CardModel {
  statusCls: string;
  badges: { text: string; cls: string }[];
  meta: string;
  headline: string;
  flags: string[];
  banner: string | null;
  rows: CardRow[];
  nights: CardNight[];
  excluded: { date: string; reasons: string }[];
  excludedCounts: string;
  tolerance: { date: string; text: string; cls: string }[];
  resources: string[];
  narrative: string;
  actions: string[];
  foot: string;
}
export function cardModel(card: SignalCard, opts?: { actions?: boolean; sample?: boolean }): CardModel;
export const CONF_TEXT: Record<string, string>;
