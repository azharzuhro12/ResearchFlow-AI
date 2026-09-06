# ResearchFlow AI — Frontend

Next.js 16 (App Router) + React 19 + TypeScript + Tailwind CSS 4.

```bash
npm install
echo 'NEXT_PUBLIC_API_BASE_URL=http://localhost:8010' > .env.local
npm run dev        # http://localhost:3000
```

The UI exposes the full pipeline: research form → plan → sources → RAG
indexing/retrieval → grounded synthesis with validated citations → report
downloads (Markdown/PDF) → scheduled research with execution history and
notification status → the persisted report catalog.

Full project documentation: see the [root README](../README.md).
