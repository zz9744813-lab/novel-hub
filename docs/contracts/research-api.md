# Research Scraping API Specification

This document defines the REST API contract for NovelForge's external research scraping feature. It is designed to be compatible with the So Novel web content extraction tool's functionality, adapted for Python/FastAPI implementation.

## Base URL

```
http://localhost:8000/api/research
```

---

## Table of Contents

- [Sources Management](#sources-management)
- [Task Creation & Status](#task-creation--status)
- [Export Operations](#export-operations)

---

## Sources Management

### List Research Sources

Retrieve all configured research source rules from the system.

**Endpoint:** `GET /api/research/sources`

**Response:** `200 OK`

```json
[
  {
    "name": "起点中文网",
    "base_url": "https://www.qidian.com",
    "chapter_list_selector": "ul.send-list li a",
    "title_selector": "h1.title",
    "content_selector": "div.chapter-content",
    "pagination_selector": "a.next-page",
    "output_format": "txt",
    "encoding": "utf-8",
    "rate_limit": 1.0,
    "description": "Mainstream Chinese web novel platform",
    "tags": ["novel", "fiction", "chinese"]
  }
]
```

**Fields:**
| Field | Type | Description |
|---|---|---|
| name | string | Human-readable source identifier |
| base_url | string | Root URL of target website |
| chapter_list_selector | string | CSS/XPath selector for chapter listing |
| title_selector | string | Selector for extracting chapter titles |
| content_selector | string | Selector for main body text |
| pagination_selector | string? | Optional selector for next-page navigation |
| output_format | enum["epub","pdf","txt"] | Default export format |
| encoding | string | Character encoding hint (utf-8/gb2312/big5) |
| rate_limit | float | Max requests per second (0 = unlimited) |
| description | string? | Optional human-readable description |
| tags | string[] | Category tags for filtering |

### Add New Research Source

Register a new source configuration rule.

**Endpoint:** `POST /api/research/sources`

**Request Body:**
```json
{
  "name": "起点中文网",
  "base_url": "https://www.qidian.com",
  "chapter_list_selector": "ul.send-list li a",
  "title_selector": "h1.title",
  "content_selector": "div.chapter-content",
  "pagination_selector": "a.next-page",
  "output_format": "txt",
  "encoding": "utf-8",
  "rate_limit": 1.0
}
```

**Response:** `201 Created`

Same JSON structure as request body.

---

## Task Creation & Status

### Create Scraping Task

Start a new research scraping job.

**Endpoint:** `POST /api/research/tasks`

**Request Body:**
```json
{
  "source_id": "起点中文网",
  "target_url": "https://www.qidian.com/work/123456"
}
```

**Response:** `202 Accepted`

```json
{
  "id": "task_abc123",
  "source_id": "起点中文网",
  "target_url": "https://www.qidian.com/work/123456",
  "status": "pending",
  "progress": 0,
  "chapters_scraped": 0,
  "error_message": null,
  "created_at": "2026-08-20T10:30:00Z",
  "updated_at": "2026-08-20T10:30:00Z"
}
```

### Get Task Status

Poll the current status of an active scraping task.

**Endpoint:** `GET /api/research/tasks/{task_id}`

**Response:** `200 OK`

Same JSON structure as task creation response.

**Status Values:**
- `"pending"` - Task queued, not yet started
- `"running"` - Currently executing scrape operation
- `"completed"` - All chapters extracted successfully
- `"failed"` - Error occurred during execution

**Error Handling:**
When status is `"failed"`, `error_message` contains a human-readable error description.

### Cancel Task

Stop an active scraping job.

**Endpoint:** `DELETE /api/research/tasks/{task_id}`

**Response:** `200 OK`

```json
{"message": "Task abc123 cancelled"}
```

### List All Tasks

Retrieve all tasks currently in the system.

**Endpoint:** `GET /api/research/tasks`

**Response:** `200 OK`

Array of task objects (same structure as individual task).

---

## Export Operations

### Export Task Results

Convert completed scraping results to specified format.

**Endpoint:** `POST /api/research/tasks/{task_id}/export`

**Query Parameters:**
- `format` (required): Output format - `epub` | `pdf` | `txt`

**Response:** `200 OK`

```json
{
  "task_id": "task_abc123",
  "total_chapters": 42,
  "total_words": 186432,
  "export_formats": {
    "txt": "/exports/task_abc123.txt",
    "epub": "/exports/task_abc123.epub"
  },
  "metadata": {
    "source_name": "起点中文网",
    "start_url": "https://www.qidian.com/work/123456",
    "generated_at": "2026-08-20T10:35:00Z"
  }
}
```

---

## Error Responses

### 404 Not Found

Returned when requesting non-existent resources.

```json
{
  "detail": "Source '起点中文网' not found"
}
```

### 400 Bad Request

Returned when task is not in "completed" status or request validation fails.

```json
{
  "detail": "Cannot export incomplete task (status: running)"
}
```

---

## Rate Limiting

Each research source has its own `rate_limit` field specifying max requests per second. The default value is `0.5` (1 request per 2 seconds) for most sources.

### Client-Side Best Practices

1. Use exponential backoff when retrying failed tasks
2. Don't poll faster than every 2 seconds
3. Respect `Retry-After` headers in 429 responses

---

## Data Persistence

Tasks and scraped chapters are persisted in SQLite database:

- `research_cache.db` - Main database file
- Tables:
  - `research_tasks` - Task metadata and status
  - `scraped_chapters` - Individual chapter records

---

## Version History

| Version | Date | Changes |
|---|---|---|
| v1.0 | 2026-08-20 | Initial release |
