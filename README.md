# LeafRoute

LeafRoute is a Melbourne walking route planner built around climate action. It cuts emissions by making walking the easy choice instead of driving, and it helps people cope with the heat, sun, and air quality that are already here by routing them through shade and cooler streets in real time, using live public data .

## What it does

- Plans real walking routes over Melbourne's pedestrian network, with up to three different options: fastest, shadiest, and quietest, each backed by real differences in tree canopy or live foot traffic.
- Shows live conditions for any destination: temperature, UV index, air quality, nearby foot traffic, and tree canopy, pulled from public data sources at request time.
- Leans harder into shaded routing automatically as the temperature rises, and points walkers to the nearest public drinking fountain on a hot day.
- Tracks distance walked and CO2e emissions avoided while a walk is in progress, and turns the total into something concrete once it's done, like a tree absorbing carbon for a day, instead of a bar figure.
- Remembers a signed in user's preferences (heat sensitivity, quieter streets, walking pace) and uses them to recommend a specific route option.
- Keeps a running public total of distance walked and emissions avoided across every walk ever logged, signed in or not, shown on the landing page.

## Architecture

The frontend is a Next.js 16 app using React 19, TypeScript, and Tailwind CSS 4, deployed on Vercel. Route planning and the machine learning inference sit alongside it as Python functions under `api/`, also running on Vercel.

The walkable street network is a graph built by merging City of Melbourne's own pedestrian network dataset with an OpenStreetMap extract, joined with proximity based node snapping since the two datasets were digitised independently and don't share exact coordinates at the same intersection. Routes are computed with Dijkstra's algorithm over that graph, biased by live tree canopy density for shaded routing and by a real XGBoost model for quieter routing. That model is trained on Melbourne's public pedestrian counting sensors and served live at request time.

Weather, UV index, and air quality come from Open-Meteo. Tree canopy coverage comes from Melbourne's urban tree dataset. Public drinking fountain locations come from the City of Melbourne's open data portal.

Supabase provides authentication, and Postgres (via Supabase) stores saved places, preferences, and walk history, with row level security scoping every user's data to themselves.

```
src/app/          Next.js App Router pages and API routes
src/components/    UI components
src/lib/           Client and server utilities, Supabase clients, providers
api/               Python route planning and ML inference functions
ml/                Model training pipeline, datasets, and evaluation
supabase/          Database migrations
```

## Getting started

Install dependencies and start the dev server:

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

The Python route planning and ML inference functions only run on Vercel's platform, so a local `npm run dev` session covers the frontend and any TypeScript API routes, but route planning itself needs a Vercel deployment (or `vercel dev`) to actually respond.

### Environment variables

Copy `.env.local` (or create one) with:

```
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=
SUPABASE_SECRET_KEY=
NEXT_PUBLIC_SITE_URL=
RESEND_API_KEY=
CONTACT_INBOX_EMAIL=
```

`SUPABASE_SECRET_KEY` is server only and should never be exposed to the client. `RESEND_API_KEY` and `CONTACT_INBOX_EMAIL` are only needed for the marketing site's contact form.

### Database

Migrations live in `supabase/migrations`. With the Supabase CLI linked to a project:

```bash
supabase db push --linked
```

For local development against a local Supabase instance:

```bash
npm run supabase:start
npm run supabase:reset
```

## Scripts

- `npm run dev` - start the local development server
- `npm run build` - create a production build
- `npm run start` - serve the production build
- `npm run lint` - run ESLint
- `npm run test` - run Playwright end to end tests
- `npm run context:drift` - validate project context chunks and check their source freshness
- `npm run supabase:start` / `supabase:stop` / `supabase:reset` / `supabase:status` - manage a local Supabase instance

## Data sources

- City of Melbourne open data: pedestrian network, pedestrian counting sensors, urban tree canopy, drinking fountains
- OpenStreetMap, via the Overpass API, for street coverage beyond the City of Melbourne's own network export
- Open-Meteo, for weather, UV index, and air quality

## License

Built for a hackathon. No license has been chosen yet.
