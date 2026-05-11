# Novel Hub v14 API Routes

所有需要登录的页面/API 都会检查 session。实验模块默认由 feature flag 关闭，关闭时返回 `404`。

## Core

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Dashboard |
| `GET` | `/login` | Login page |
| `POST` | `/login` | Login, limited to `5/minute` |
| `GET` | `/settings` | Runtime/settings page |
| `POST` | `/settings/ai` | Save encrypted AI settings |
| `POST` | `/settings/reindex` | Rebuild indexes |

## Projects & Chapters

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/projects/{project}` | Project detail |
| `POST` | `/projects/{project}/chapters/new` | Create chapter |
| `GET` | `/projects/{project}/editor/{filename}` | Desktop editor |
| `GET` | `/projects/{project}/chapters/{filename}/read` | Mobile/read-only view |
| `POST` | `/projects/{project}/chapters/{filename}` | Save chapter |
| `GET` | `/projects/{project}/search` | Project search |
| `GET` | `/projects/{project}/timeline` | Project timeline page |
| `GET` | `/api/projects/{project}/timeline` | Project timeline API |
| `GET` | `/projects/{project}/export` | Export page |
| `GET` | `/api/projects/{project}/export` | Export TXT/EPUB |

## Entities

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/projects/{project}/entities` | Entity list |
| `GET` | `/projects/{project}/entities/{ent_id}` | Entity detail |
| `GET` | `/projects/{project}/entities/{ent_id}/arc` | Entity arc page |
| `GET` | `/api/entities/{ent_id}/arc` | Entity arc API |
| `GET` | `/projects/{project}/graph` | Entity graph page |
| `GET` | `/api/projects/{project}/graph` | Entity graph API |
| `GET` | `/projects/{project}/threads-board` | Threads board page |
| `GET` | `/api/projects/{project}/threads-board` | Threads board API |
| `POST` | `/api/projects/{project}/threads` | Create thread |
| `PUT` | `/api/projects/{project}/threads/{filename}` | Update thread |
| `DELETE` | `/api/projects/{project}/threads/{filename}` | Delete thread |
| `GET` | `/api/entities` | List/search entities |
| `POST` | `/api/entities` | Create entity |
| `GET` | `/api/entities/{ent_id}` | Entity JSON |
| `PUT` | `/api/entities/{ent_id}` | Update entity |
| `DELETE` | `/api/entities/{ent_id}` | Delete entity |
| `POST` | `/api/entity-relations` | Create relation |
| `DELETE` | `/api/entity-relations/{rel_id}` | Delete relation |

## Snapshots

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/projects/{project}/snapshots` | Create manual snapshot |
| `GET` | `/api/snapshots/{snap_id}/diff` | Show snapshot diff |

## Feature-Gated Advanced Routes

| Flag | Method | Endpoint | Description |
|---|---|---|---|
| `NOVELHUB_FEATURE_AI` | `GET` | `/api/projects/{project}/ai/generate` | AI streaming generation |
| `NOVELHUB_FEATURE_AI` | `POST` | `/api/projects/{project}/ai/outline/volume` | Generate volume synopsis |
| `NOVELHUB_FEATURE_AI` | `POST` | `/api/projects/{project}/ai/outline/chapter` | Generate chapter outlines |
| `NOVELHUB_FEATURE_AI` + `NOVELHUB_FEATURE_SCENES` | `POST` | `/api/projects/{project}/ai/outline/scene` | Split chapter into scenes |
| `NOVELHUB_FEATURE_AI` + `NOVELHUB_FEATURE_SCENES` | `POST` | `/api/projects/{project}/ai/outline/draft` | Expand scene draft |
| `NOVELHUB_FEATURE_SCENES` | `GET` | `/api/projects/{project}/scenes` | Scene list |
| `NOVELHUB_FEATURE_SCENES` | `POST` | `/api/projects/{project}/scenes` | Create scene |
