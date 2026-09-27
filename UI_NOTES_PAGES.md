# UI notes — kiosk, inbox, watcher, family pages

Collected while walking the four pages on local demo data (branch `justin/ui-app`).
Status: DONE = changed on this branch; NEEDS <owner> = someone else's file.

| # | Screen | What | Why | Priority | Status |
|---|--------|------|-----|----------|--------|
| 1 | Kiosk (all modes) | All kiosk text in Nunito, the logo's font: the full variable font (`fonts/Nunito-Variable-latin.woff2`, weights 200-1000, bundled offline) replaces the digits-only 800 subset; every element keeps its own weight; buttons inherit it. | One brand voice on the bedside screen; the wordmark and the words now match. | Medium | DONE |
| 2 | Kiosk (night) | At night the colour logo is swapped for the one-colour white logo from the brand kit (`docs/brand/mono/irin-horizontal-white.svg` -> `display/brand/irin-logo-white.svg`), dimmed to 0.35. | A dim white mark suits the dark night screen better than the sage/mint logo. | Medium | DONE |
