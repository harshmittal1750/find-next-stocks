# Web dashboard

The dashboard is a Next.js App Router application. The page is a Server Component that reads
the FastAPI JSON endpoint; the stock explorer is the smallest client-side interactive boundary.
The API reads the tracked normalized JSON snapshot and applies ownership bounds before serving it.
If the API is unavailable, the dashboard renders an explicit offline/empty state.

```bash
npm install
npm run dev
```

Set `API_BASE_URL` to change the server-side API origin. The default is
`http://127.0.0.1:8000`.
