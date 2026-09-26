# frontend/app — the hosted four-tab app

Vite + React + TypeScript + Tailwind (decision 28). Node 22.

```bash
npm install        # once; commit package-lock.json
npm run types      # regenerates src/types/*.d.ts from the Pi's (:8000) and the relay's (:8100) /openapi.json
npm run dev        # http://localhost:5173
```

Rules:
- `dist/` is COMMITTED: rebuild (`npm run build`) and commit it before every deploy; Caddy serves it and the server never runs Node. The placeholder `dist/index.html` is replaced by the first build.
- `.env.production` (committed, public URLs) is what the build bakes in; `.env.development.local` (gitignored) holds local values, see `.env.example`.
- Adding a dependency is Justin's decision. An agent may run `npm run build`, `npm run types`, and `npx tsc --noEmit`, never `npm install <new package>` unprompted.
- Message shapes come from the generated types; the app never hand-declares one.
