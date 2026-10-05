# jobsearch stack — agent-facing job-search gateway

Self-hosted service that lets agents (Hermes Agent, Claude Code, any HTTP or
MCP client) search job postings through a single authenticated API —
without a paid search API and without behaving like a bot.

```
                   ┌────────────────────────── LXC (internal LAN only) ──────────────────────┐
                   │  ┌──────────────────┐      ┌────────────────────────────────────────┐  │
  Hermes Agent ────MCP──▶                   │      │ jobsearch-gateway (FastAPI, :8080)     │  │
  Claude Code  ────MCP──▶  mcp/jobsearch_   │─────▶│  /v1/search  /v1/jobs  /v1/jobs/{id}  │  │
  plain agent  ────REST─▶  mcp.py (on the   │      │  /v1/sources  /health                │  │
                        │  AGENT host)     │      └───────┬──────────────────┬───────────┘  │
                        └──────────────────┘              │ SQLite           │               │
                                                          │ /data/jobs.db    │               │
                                                          └──────────┐       │               │
                                                   ┌─────────────────┐ ┌──▼───────────┐   │
                                                   │ SearXNG (JSON)  │ │ BA jobsuche  │   │
                                                   │ no published    │ │ REST API     │   │
                                                   │ port            │ │ (official)   │   │
                                                   └─────────────────┘ └──────────────┘   │
                                                   └──────────────────────────────────────────┘
```

## The bot-detection question, answered tier by tier

There is no "trick the WAF" layer — the stack is designed so it rarely has to:

| Tier | Source                                | Why it doesn't trip bot detection                                                                         |
| ---- | ------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| 1    | Bundesagentur für Arbeit jobsuche API | Official public API. Germany's largest board, no registration, no rate walls.                             |
| 2    | Self-hosted SearXNG                   | JSON meta-search; rotates upstream engines and spreads requests. Your instance, your rate.                |
| 3    | Scrape fallback (v2, stub)            | Scrapling `StealthyFetcher` for the few boards without an API — deliberately not built yet (see Roadmap). |

Politeness is part of the design: per-source throttles (1 req/s to the BA, 
2 req/s to SearXNG), 60-min search cache, 24-h details cache, honest
`jobsearch-gateway/0.1.0 (homelab)` User-Agent on API calls.

## Quickstart (on the Proxmox LXC)

```bash
cd stacks/jobsearch
cp .env.example .env
openssl rand -hex 32   # -> JOBSEARCH_API_TOKEN
openssl rand -hex 32   # -> SEARXNG_SECRET
chmod 600 .env         # and put both values into .env
docker compose up -d --build
./deploy/verify.sh http://<lxc-ip>:8080 <JOBSEARCH_API_TOKEN>
```

Full LXC setup (nesting, keyctl, vfs fallback): [deploy/proxmox-lxc.md](deploy/proxmox-lxc.md)

## REST API

All `/v1/*` routes need `Authorization: Bearer $JOBSEARCH_API_TOKEN`.
`/health` is open for the compose healthcheck.

```bash
# search (fans out to all enabled sources, dedups, persists)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"Netzwerkadministrator","location":"München","radius_km":25,"size":25}' \
  http://192.168.30.10:8080/v1/search

# filter persisted jobs
curl -H "Authorization: Bearer $TOKEN" \
  "http://192.168.30.10:8080/v1/jobs?q=netzwerk&remote=true&limit=50"

# full details of one job (24h cache)
curl -H "Authorization: Bearer $TOKEN" \
  http://192.168.30.10:8080/v1/jobs/bundesagentur:10001-1002681784-S

# source health (agents can degrade gracefully)
curl -H "Authorization: Bearer $TOKEN" http://192.168.30.10:8080/v1/sources
```

Search request fields: `query`, `location`, `radius_km`, `page`, `size`,
`sources` (subset), `age_days`, `persist`. A failing source returns
`"status": "error"` for that source only — the search itself still returns 200.

Normalized `Job` fields: `id` (`{source}:{refnr}`), `source`, `refnr`,
`title`, `company`, `location{city,postal_code,country,raw}`, `published`,
`url`, `details_url`, `salary`, `remote`, `employment_type`,
`description_snippet`, `fetched_at`, `hash`.

## Agent integration

### Claude Code (`.mcp.json` in the project, or user scope)

```json
{
  "mcpServers": {
    "jobsearch": {
      "command": "uv",
      "args": ["run", "--with", "fastmcp,httpx", "python",
               "/path/to/claude-network-skills/stacks/jobsearch/mcp/jobsearch_mcp.py"],
      "env": {
        "JOBSEARCH_GATEWAY_URL": "http://192.168.30.10:8080",
        "JOBSEARCH_API_TOKEN": "<same token as the stack .env>"
      }
    }
  }
}
```

### Hermes Agent

Register the same stdio MCP server in your Hermes MCP toolset config — the
wrapper speaks vanilla stdio MCP, so any MCP-capable agent can mount it
identically. Set the same two env vars (`JOBSEARCH_GATEWAY_URL`,
`JOBSEARCH_API_TOKEN`). Alternatively, Hermes can skip MCP and call the REST
API directly with its HTTP tool.

### Tools the wrapper exposes

- `search_jobs(query, location, radius_km, sources, limit, age_days, persist)`
  — compact list (id, title, company, location, published, url) + raw JSON
- `get_job(job_id)` — full description text
- `list_sources()` — tier + health of each source

### Testing with agents

- Claude Code: after registering, run `/mcp` → `jobsearch` listed. Then:
  *"search_jobs for Netzwerkadministrator in München, 25 km, show the first 5
  with company and published date"* → *"get_job for the first result and
  summarize the requirements"*.
- Hermes: same two prompts through its MCP toolset.
- Any HTTP agent: the curl snippet above.

## Configuration (`.env`)

| Var                       | Purpose                                                           |
| ------------------------- | ----------------------------------------------------------------- |
| `JOBSEARCH_API_TOKEN`     | Bearer token for the gateway                                      |
| `SEARXNG_SECRET`          | SearXNG instance secret                                           |
| `JOBSEARCH_SOURCES`       | Enabled sources (default `bundesagentur,searxng` )                |
| `SEARXNG_SITE_FILTERS`    | Optional: restrict searxng to specific boards (`site:` OR-groups) |
| `JOBSEARCH_EURES_API_KEY` | Optional: enables the (stub) eures connector                      |

## Adding a source

One module in `gateway/jobsearch_gateway/sources/` implementing
`search() / get_details() / health()` (subclass `SourceConnector`), one line
in `sources/__init__.py::build_connectors`, one throttle/timeout entry.
`main.py` never changes. Build the field mapping from one live response —
that rule is why the BA connector survived its first deploy.

## Roadmap

- **v2 — scrape fallback** (`sources/scrape.py`): Scrapling `StealthyFetcher`
  behind the `scrape` compose profile, for boards that have no API and block
  plain HTTP. Deferred deliberately: it drags a Chromium-based image (~500 MB+)
  into a stack that currently fits a 2 GB LXC, and Tier 1+2 already cover
  BA (Germany's largest board) plus the open web.
- **EURES connector**: fully wired but stubbed until a registered API key and
  one live request fill in the endpoint (see `sources/eures.py`).
- Possible: per-agent tokens, scheduled watches with webhook notify
  ("new postings matching X in the last 24 h").

## Legal/etiquette note

The BA API key is the public client key documented by the
[bundesAPI/jobsuche-api](https://github.com/bundesAPI/jobsuche-api) project.
The API is unofficial (reverse-engineered from the official app) and can
change without notice — `verify.sh` catches that on deploy. Keep the
throttles; this stack is for personal job search, not bulk scraping.


#####################################################################################


Fertig. Das Setup-Kit stacks/jobsearch/ ist komplett gebaut, getestet und committet (887a649).

Zusammenfassung — die Antworten auf deine Fragen

„Beste Möglichkeit ohne als Bot erkannt zu werden" — gestaffelt statt getrickst:
1. Tier 1: Offizielle APIs — Bundesagentur-für-Arbeit-Jobsuche-API (kostenlos, keine Registrierung) hat überhaupt kein Bot-Problem und deckt Deutschlands größtes Board ab
2. Tier 2: Self-hosted SearXNG — JSON-Metasuche, rotiert die Upstream-Engines
3. Tier 3: Scraping-Fallback (v2-Stub) — Scrapling StealthyFetcher nur für Boards ohne API, bewusst zurückgestellt (Chromium würde das 2-GB-Budget sprengen)

„Am schnellsten, self-hosted, kostenlos" — FastAPI-Gateway + SQLite + SearXNG in einem Docker-Compose-Stack, ~1 h Deploy, nur Kosten fürs LLM. Politeness eingebaut: 1 req/s zur BA, 2 req/s zu SearXNG, Caches (60 min Suche / 24 h Details).

„Optimales Proxmox-Setup mit Agenten-Anbindung" — unprivilegierter LXC (2 GB, nesting+keyctl, Gotchas dokumentiert), Gateway auf Port 8080 nur im LAN. Agenten sprechen ihn dreifach an: MCP-Wrapper (search_jobs, get_job, list_sources — identisch für Hermes und Claude Code registrierbar) oder direkt REST.

Live verifiziert (nicht nur Syntax)

- BA-API-Feld-Mapping aus echten v6-Antworten gebaut — 5 normalisierte Jobs, Details-Endpoint mit 1382-Zeichen-Beschreibung
- Komplette API gegen laufendes uvicorn getestet: 401-Auth ✓, Fan-out ✓, Dedup (10→5) ✓, Graceful Degradation (SearXNG down → trotzdem 200) ✓, Details-Cache ✓, Persistenz ✓, Source-Filterung ✓
- Der E2E-Test fing einen echten Bug (zip-Unpacking in sources) plus drei Designfehler (Env-Prefix, Healthcheck-Polling der BA, /data-Rechte) — alle gefixt

Deine nächsten Schritte

1. deploy/proxmox-lxc.md auf deinem Proxmox durchgehen → LXC erstellen, Docker installieren, docker compose up -d --build
2. ./deploy/verify.sh http://<lxc-ip>:8080 <TOKEN> — einmal im LXC, einmal von einem anderen Rechner
3. MCP-Server bei Hermes und/oder Claude Code registrieren (Snippets im Kit-README) und testen: "search_jobs für Netzwerkadministrator in München, zeige die ersten 5"

Zwei ehrliche Einschränkungen: Die BA-API ist inoffiziell (reverse-engineered, kann sich ändern — verify.sh Check 3 fängt das ab), und der SearXNG-Scrape-Fallback für Boards wie StepStone ist Roadmap, nicht ausgebaut. Der Commit ist lokal auf main — sag Bescheid, wenn ich pushen soll.

✻ Cooked for 17m 7s · done 3:53 AM

※ recap: Ziel: selbstgehostete Stellensuche für Agenten (Hermes/Claude Code) auf Proxmox. Das Kit `stacks/jobsearch/` ist fertig gebaut, live getestet und als Commit 887a649 lokal committet. Nächster Schritt: Deployment auf deinem Proxmox-LXC gemäß `deploy/proxmox-lxc.md`.

