Deep-dive into a WIP component or library. Use this to build understanding before modifying code.

### Usage

Specify the target: `/understand registry`, `/understand wip-auth`, `/understand wip-client`, etc.

If no target is specified, ask the user which component they want to understand.

### Steps

#### 1. Locate the component
Map the target name to its directory:
- `registry` → `components/registry/`
- `def-store` → `components/def-store/`
- `template-store` → `components/template-store/`
- `document-store` → `components/document-store/`
- `reporting-sync` → `components/reporting-sync/`
- `ingest-gateway` → `components/ingest-gateway/`
- `mcp-server` → `components/mcp-server/`
- `wip-auth` → `libs/wip-auth/`
- `wip-client` → `libs/wip-client/`
- `wip-react` → `libs/wip-react/`
- `wip-proxy` → `libs/wip-proxy/`
- `console` → `ui/wip-console/`

#### 2. Read the README (if it exists)
Check for `README.md` in the component directory.

#### 3. List the source structure
List the `src/` directory (Python) or `src/` directory (TypeScript) to understand the module layout.

#### 4. Read route/endpoint definitions
- **Python services:** Find the FastAPI app — look for `app = FastAPI(...)` and `@app.post`, `@app.get`, etc. Usually in `src/main.py` or `src/app.py`.
- **TypeScript libs:** Read `src/index.ts` for the public API surface.

#### 5. Read models and schemas
- **Python:** Look for Pydantic models in `src/models.py` or `src/schemas.py`
- **TypeScript:** Look for type definitions in `src/types.ts` or similar

#### 6. List tests
List the `tests/` directory. Note the test file names — they indicate what behaviours are tested.

#### 7. Read infrastructure config
- `Dockerfile` — base image, dependencies, entry point
- Check `docker-compose/base.yml` or `docker-compose/modules/` for the service's compose entry
- Note environment variables, ports, volumes

#### 8. Summarize
Present a structured summary:

```
Component: registry
Directory: components/registry/

Endpoints:
- POST /api/registry/entries — register entities (bulk)
- GET /api/registry/entries/{id} — get entry by ID
- ...

Models:
- RegistryEntry — id, entity_type, composite_key, namespace, ...
- BulkResponse — results, total, succeeded, failed

Dependencies:
- MongoDB (primary store)
- wip-auth (authentication)

Tests: 15 files, covering [key areas]

Key patterns:
- [Notable implementation details]
```

### When to use
- Before modifying a component you haven't worked on
- When investigating a bug that spans multiple services
- To understand how a feature is implemented across the stack
