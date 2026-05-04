# Novel Hub C-Route API Reference (v6)

## 1. Entities Management
Manage characters, locations, items, threads, etc.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/entities` | List entities. Params: `project`, `kind`, `q` (search). |
| `POST` | `/api/entities` | Create a new entity. |
| `GET` | `/api/entities/{id}` | Get entity details. |
| `PUT` | `/api/entities/{id}` | Update entity metadata/properties. |
| `DELETE` | `/api/entities/{id}` | Remove entity. |

### Request/Response Schemas
- **POST /api/entities**:
    - Request: `{ "project": "str", "kind": "str", "name": "str", "aliases": ["str"], "properties": {} }`
    - Response: `{ "status": "ok", "id": "ent_xxxx" }`

---

## 2. Entity Relations
Track relationships between entities.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/entity-relations` | Create a relationship. |
| `DELETE` | `/api/entity-relations/{id}` | Delete a relationship. |

- **POST /api/entity-relations**:
    - Request: `{ "source_id": "ent_1", "target_id": "ent_2", "relation_type": "family_of", "notes": "str" }`

---

## 3. Scenes & Outline
Manage scene-level breakdown within chapters.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/projects/{p}/scenes` | List scenes for a chapter. |
| `POST` | `/api/projects/{p}/scenes` | Manual scene creation/extraction. |
| `PUT` | `/api/scenes/{id}` | Update scene metadata (POV, summary, etc). |
| `DELETE` | `/api/scenes/{id}` | Merge scene back or delete. |
| `GET` | `/api/projects/{p}/outline` | Get full project outline tree. |

---

## 4. Analysis & Views
Advanced writing tools and visualizations.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/projects/{p}/timeline` | Timeline data. Params: `lane` (pov/char/status). |
| `GET` | `/api/projects/{p}/threads-board` | Plot hole / Thread kanban data. |
| `GET` | `/api/entities/{id}/appearances` | Reverse lookup for entity occurrences. |

---

## 5. Snapshots (History)
Integrated version control.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/projects/{p}/snapshots` | Create manual snapshot. |
| `GET` | `/api/projects/{p}/snapshots` | List snapshots for a chapter. |
| `POST` | `/api/snapshots/{id}/restore` | Rollback to a specific snapshot. |

- **GET /api/projects/{p}/snapshots**:
    - Response: `[{ "id": 1, "created_at": "...", "label": "...", "hash": "..." }]`
