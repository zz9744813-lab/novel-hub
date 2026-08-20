# Research Scraping API Specification (v1.0)

NovelForge's external research scraping functionality integrated from So Novel rules engine.

**Base URL**: `http://localhost:8000/api/research`  
**Authentication**: Bearer token required in production (`ADMIN_API_TOKEN`)

---

## Quick Start with curl Examples

### 1. List Available Sources

```bash
curl -X GET "http://localhost:8000/api/research/sources" \
  -H "Authorization: Bearer your_token_here"
```

Response:
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
    "rate_limit": 1.0
  }
]
```

### 2. Create New Scraping Task

```bash
curl -X POST "http://localhost:8000/api/research/tasks" \
  -H "Authorization: Bearer your_token_here" \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "起点中文网",
    "target_url": "https://www.qidian.com/work/123456/chapter-1"
  }'
```

Response:
```json
{
  "id": "task_abc123",
  "source_id": "起点中文网",
  "target_url": "https://www.qidian.com/work/123456/chapter-1",
  "status": "pending",
  "progress": 0,
  "chapters_scraped": 0,
  "error_message": null,
  "created_at": "2026-08-20T10:30:00Z",
  "updated_at": "2026-08-20T10:30:00Z"
}
```

### 3. Monitor Task Progress

Poll the task status endpoint every 2 seconds during active scraping:

```bash
# Poll for updates
curl -X GET "http://localhost:8000/api/research/tasks/task_abc123" \
  -H "Authorization: Bearer your_token_here"
```

Typical progress flow:
```json
{"status":"running","progress":15,"chapters_scraped":3}
{"status":"running","progress":45,"chapters_scraped":8}
{"status":"completed","progress":100,"chapters_scraped":25}
```

### 4. Export Results to TXT Format

```bash
curl -X POST "http://localhost:8000/api/research/tasks/task_abc123/export?format=txt" \
  -H "Authorization: Bearer your_token_here"
```

Response:
```json
{
  "task_id": "task_abc123",
  "total_chapters": 25,
  "total_words": 125000,
  "export_formats": {
    "txt": "/exports/task_abc123.txt",
    "epub": "/exports/task_abc123.epub"
  },
  "metadata": {...}
}
```

---

## Complete Workflow Example

```bash
# Step 1: Create task
TASK_ID=$(curl -s -X POST "http://localhost:8000/api/research/tasks" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_id":"起点中文网","target_url":"https://example.com/novel/ch1"}' \
  | jq -r .id)

echo "Created task: $TASK_ID"

# Step 2: Poll until completed
while true; do
  STATUS=$(curl -s "http://localhost:8000/api/research/tasks/$TASK_ID" | jq -r .status)
  PROGRESS=$(curl -s "http://localhost:8000/api/research/tasks/$TASK_ID" | jq -r .progress)
  
  echo "Status: $STATUS, Progress: ${PROGRESS}%"
  
  if [ "$STATUS" = "completed" ]; then
    break
  fi
  
  sleep 2
done

# Step 3: Export results
curl -X POST "http://localhost:8000/api/research/tasks/$TASK_ID/export?format=epub" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## Rate Limiting Strategy

Each research source has its own `rate_limit` configuration:
- Default: 0.5 requests/second (2 second delay between requests)
- Maximum supported: 10 requests/second
- Violation returns `429 Too Many Requests`

**Best Practices**:
1. Implement exponential backoff on client side
2. Respect `Retry-After` header in 429 responses
3. Don't poll faster than every 2 seconds per task

---

## Error Responses

### 400 Bad Request
```json
{
  "detail": "Cannot export incomplete task (status: running)"
}
```

### 401 Unauthorized  
```json
{
  "detail": "unauthorized"
}
```

### 404 Not Found
```json
{
  "detail": "Source 'Unknown Source' not found"
}
```

### 429 Too Many Requests
```json
{
  "detail": "Rate limit exceeded for this source",
  "retry_after": 60
}
```

---

## Validation Rules

### Task Creation

**Required Fields**:
- `source_id`: Must exist in configured sources
- `target_url`: Valid HTTP/HTTPS URL format

**Common Validation Errors**:
- `"Source not found"` → Source ID doesn't match any configuration
- `"Invalid URL format"` → URL missing scheme or malformed
- `"Network timeout"` → Target website unreachable

---

## Database Schema

Tasks are persisted in SQLite at `data/research_cache.db`:

```sql
CREATE TABLE research_tasks (
    task_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_url TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    progress INTEGER DEFAULT 0,
    chapters_scraped INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

---

## Testing Locally

Use pytest for automated testing:

```bash
cd novel-hub/backend
python -m pytest tests/test_research_e2e.py -v --cov=app
```

Tests cover:
- Task creation with mock HTTP responses
- Progress polling simulation  
- Export functionality validation
- Reference conversion workflow

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| v1.0 | 2026-08-20 | Initial release with core scraping & reference import |
