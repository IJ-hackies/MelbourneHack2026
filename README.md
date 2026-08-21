# HeatRoute

Empty frontend scaffold for HeatRoute, built with Next.js, React, TypeScript, and Tailwind CSS.

## Getting Started

Install dependencies and run the development server:

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page in `src/app/page.tsx`. The page auto-updates as you edit the file.

## Scripts

- `npm run dev` — start the local development server
- `npm run build` — create a production build
- `npm run start` — serve the production build
- `npm run lint` — run ESLint
- `npm run context:drift` — validate project context chunks and check their source freshness

## Project notes

The product vision and technical direction live in
`Context/Chunks/heatroute.md`. Future coding agents can load the project-owned
context through the `recontext`, `reupdate`, and `reaudit` skills. Codex uses
the canonical definitions under `.agents/skills/`; Claude Code uses the
mirrored definitions under `.claude/skills/`.
