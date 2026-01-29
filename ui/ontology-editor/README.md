# Ontology Editor

A Vue 3 + PrimeVue web UI for managing terminologies and terms in the Def-Store service.

## Features

- **Terminology Management**: Create, view, edit, and delete terminologies
- **Term Management**: Full CRUD operations for terms within terminologies
- **Bulk Import**: Import terms from JSON or CSV files
- **Export**: Export terminologies to JSON or CSV
- **Validation**: Validate single or multiple values against terminologies

## Tech Stack

- **Vue 3** with Composition API
- **TypeScript** for type safety
- **Vite** for build tooling
- **PrimeVue 4** for UI components
- **Pinia** for state management
- **Vue Router** for navigation

## Development Setup

### Prerequisites

- Node.js 20+
- npm or yarn
- Running Def-Store service (see components/def-store)

### Local Development

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# The app will be available at http://localhost:3000
```

The Vite dev server proxies API requests to `http://localhost:8002` (Def-Store).

### Using Docker

```bash
# Start with existing infrastructure
podman-compose -f docker-compose.dev.yml up -d

# Or build and run production container
podman-compose up -d
```

## Configuration

### API Key

The application requires an API key to communicate with Def-Store. Set it in the UI:

1. Click the user icon in the header
2. Select "Set API Key"
3. Enter your API key (development: `dev_master_key_for_testing`)

The API key is stored in localStorage.

### Environment Variables

For Docker deployments:

| Variable | Description | Default |
|----------|-------------|---------|
| `API_KEY` | API key for Def-Store | `dev_master_key_for_testing` |
| `REGISTRY_API_KEY` | API key for Registry | `dev_master_key_for_testing` |

## Project Structure

```
ontology-editor/
├── src/
│   ├── api/           # API client (axios)
│   │   └── client.ts
│   ├── components/    # Reusable Vue components
│   │   ├── AppLayout.vue
│   │   ├── TerminologyList.vue
│   │   ├── TerminologyForm.vue
│   │   ├── TermList.vue
│   │   ├── TermForm.vue
│   │   ├── BulkTermImport.vue
│   │   └── DeprecateTermDialog.vue
│   ├── router/        # Vue Router configuration
│   ├── stores/        # Pinia stores
│   │   ├── auth.ts
│   │   ├── terminology.ts
│   │   ├── term.ts
│   │   └── ui.ts
│   ├── types/         # TypeScript interfaces
│   ├── views/         # Page components
│   │   ├── HomeView.vue
│   │   ├── TerminologyListView.vue
│   │   ├── TerminologyDetailView.vue
│   │   ├── ImportView.vue
│   │   └── ValidateView.vue
│   ├── App.vue
│   └── main.ts
├── docker-compose.yml
├── docker-compose.dev.yml
├── Dockerfile
├── Dockerfile.dev
└── nginx.conf
```

## Routes

| Path | Description |
|------|-------------|
| `/` | Dashboard with stats and quick actions |
| `/terminologies` | List all terminologies |
| `/terminologies/:id` | View terminology details and terms |
| `/import` | Import terminology from file |
| `/validate` | Validate values against terminologies |

## API Endpoints

The UI communicates with the Def-Store service at `/api/def-store/`:

### Terminologies
- `GET /terminologies` - List all terminologies
- `POST /terminologies` - Create new terminology
- `GET /terminologies/{id}` - Get terminology by ID
- `PUT /terminologies/{id}` - Update terminology
- `DELETE /terminologies/{id}` - Delete terminology

### Terms
- `GET /terminologies/{id}/terms` - List terms
- `POST /terminologies/{id}/terms` - Create term
- `POST /terminologies/{id}/terms/bulk` - Bulk create terms
- `GET /terms/{id}` - Get term by ID
- `PUT /terms/{id}` - Update term
- `POST /terms/{id}/deprecate` - Deprecate term
- `DELETE /terms/{id}` - Delete term

### Import/Export
- `POST /import-export/import` - Import terminology with terms
- `GET /import-export/export/{id}` - Export terminology

### Validation
- `POST /validation/validate` - Validate single value
- `POST /validation/validate-bulk` - Validate multiple values

## Build for Production

```bash
# Build static assets
npm run build

# Preview production build locally
npm run preview
```

## License

Part of the World In a Pie (WIP) project.
