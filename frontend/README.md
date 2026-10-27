# EarthScape Climate Agency — web client

React 18 + TypeScript + Vite + Tailwind, backed entirely by the Django API in
`../backend`. No data is faked in the browser: every figure on a screen comes
from an API call, and an unavailable view renders the reason the platform
returned instead of a placeholder.

## Run it

```bash
npm install
npm run dev        # http://localhost:5173  (API calls proxy to 127.0.0.1:8000)
npm run lint       # tsc -b --noEmit
npm run build      # type-check + production bundle in dist/
```

`VITE_API_BASE_URL` overrides the API origin; the default is the same-origin
`/api` path used by the nginx setup in `../backend/infrastructure/nginx`.

## Layout

| Path | Contents |
| --- | --- |
| `src/app/` | router and the role-aware navigation catalogue |
| `src/layouts/` | public site, signed-in app shell, auth shell |
| `src/pages/public/` | landing, help, sign-in, registration, password recovery |
| `src/pages/app/` | analyst workspace: dashboard, analytics, history, map, anomalies, datasets, uploads, ingestion, processing, big data, ML lab, model registry, forecasts, alerts, notifications, reports, profile, support |
| `src/pages/admin/` | administration: overview, users, roles, datasets, pipelines, alert rules, health, tickets |
| `src/components/` | shared UI primitives (`ui/`), charts (`charts/`), map (`maps/`), brand/banner/loader |
| `src/features/` | auth context and guards, theme context |
| `src/services/` | `http.ts` (fetch, tokens, error mapping) and `api.ts` (typed endpoint surface) |
| `src/types/api.ts` | the response types the API contract is checked against |
| `src/hooks/`, `src/lib/` | `useAsync` and formatting/class-name helpers |

Access is enforced server-side; the client only hides what a role cannot call,
so a hidden control is never the actual protection.
