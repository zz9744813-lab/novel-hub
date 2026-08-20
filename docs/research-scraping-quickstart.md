# Research Scraping Feature - Quick Start Guide

Complete guide for setting up and using NovelForge's external research scraping functionality powered by So Novel rules engine.

---

## Prerequisites

- Python 3.10+ installed
- Node.js 18+ (for frontend development)
- SQLite 3.x (bundled with Python)

---

## Installation Steps

### Step 1: Install Backend Dependencies

```bash
cd novel-hub/backend

# Install all required Python packages
pip install httpx beautifulsoup4 lxml aiohttp pydantic fastapi uvicorn ebooklib reportlab pytest pytest-asyncio

# Verify installation
python -c "import httpx; import bs4; print('✅ All dependencies installed')"
```

### Step 2: Configure Research Sources

Create a configuration file at `backend/data/research_sources.json`:

```json
{
  "version": "1.0",
  "generated_at": "2026-08-20T10:30:00Z",
  "sources": [
    {
      "name": "起点中文网示例",
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
}
```

**Selector Tips**:
- **CSS Class**: `.chapter-title`
- **CSS ID**: `#chapter-content`  
- **Tag + Class**: `div.content`
- **Attribute**: `a[href*="/ch"]`

### Step 3: Install Frontend Dependencies

```bash
cd novel-hub/frontend

# Install additional UI libraries
npm install @hookform/resolvers zod react-hook-form

# Verify build works
npm run build
```

Expected output:
```
dist/index.html                   1.00 kB ✓gzip:   0.62 kB
dist/assets/index-*.css           61.98 kB ✓gzip:  12.93 kB
dist/assets/index-*.js           486.34 kB ✓gzip: 147.37 kB
built in X.XXs
```

---

## Running the Application

### Start Backend Server

```bash
cd novel-hub/backend

# Option A: Using uvicorn directly
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Option B: Using Python module
python -m uvicorn app.main:app --reload
```

Success indicator:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started server process [XXXX]
INFO:     Waiting for application startup.
Readiness OK: {...}
```

### Start Frontend Development Server

Open another terminal window:

```bash
cd novel-hub/frontend

npm run dev
```

Success indicator:
```
VITE ready in XXX ms
➜  Local:   http://localhost:5173/
```

---

## Using the Research Feature

### 1. Access Research Page

1. Navigate to http://localhost:5173
2. Click **"调研"** tab from sidebar navigation
3. You should see the ResearchPage with Source Selector dropdown

### 2. Create New Scraping Task

Fill out the form:

1. **Source Selection**: Choose from dropdown (e.g., "起点中文网示例")
2. **Target URL**: Enter chapter list or single chapter page URL
3. **Submit**: Click "开始调研" button

Form validation examples:

✅ Valid:
- Source: Any configured source name
- URL: `https://example.com/novel/chapter-1`

❌ Invalid:
- Empty source selection
- URL: `example.com/no-scheme` → "URL 格式不正确，请以 https:// 开头"
- Missing fields → "请选择调研源" / "请输入目标 URL"

### 3. Monitor Progress

While task is running:

- Progress bar fills smoothly (0% → 100%)
- Chapter count updates in real-time
- Spinner icon pulses until completion
- Last error messages expandable if failures occur

Status indicators:
- 🟢 Green check = Completed successfully
- 🟡 Yellow spinner = Currently scraping  
- 🔴 Red X = Failed with error details
- ⚪ Gray globe = Pending queue

### 4. Export Results

After completion, use the export endpoint:

```bash
curl -X POST "http://localhost:8000/api/research/tasks/{task_id}/export?format=epub" \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

Export formats supported:
- **TXT**: Plain text file with chapter structure
- **EPUB**: Readable eBook format (requires Calibre for viewing)
- **PDF**: A5 formatted document (professional layout)

---

## Configuration & Customization

### Adjust Rate Limiting

For faster scraping (risk of blocking), increase `rate_limit`:

```json
{
  "rate_limit": 2.0,  // Allow 2 requests per second instead of default 0.5
  ...
}
```

Recommended range: `0.5 - 5.0`

### Add Custom User-Agent

Edit `workbench/collab/research_scraper.py`:

```python
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
```

This prevents basic bot detection on some websites.

### Custom Genre Extraction Heuristics

Edit `backend/app/services/research_to_reference.py`:

The `extract_genre_hint()` method uses keyword matching. Add your own patterns:

```python
genre_keywords = {
    "奇幻": ["magic", "spell", "wizard", "dragon", "你的关键词"],
    "科幻": ["robot", "space", "alien", "technology", "你的关键词"],
    # ... etc
}
```

For production-grade genre detection, replace with BERT/NLP model integration.

---

## Debugging Mode

Enable detailed logging during scraping:

```bash
# Terminal 1: Backend with debug logs
uvicorn app.main:app --reload --log-level debug

# Terminal 2: Frontend
npm run dev
```

Debug output shows:
- Each HTTP request URL being fetched
- Chapter extraction results
- Error stack traces for parsing failures

---

## Common Issues & Solutions

### Issue 1: "Failed to fetch URL"

**Cause**: Target website blocks scraper's User-Agent

**Solution**: Rotate User-Agents or add proxy support

### Issue 2: "No chapters found"

**Cause**: CSS selectors don't match current page structure

**Solution**: 
1. Open browser DevTools
2. Right-click target chapter → Inspect Element
3. Copy CSS selector
4. Update `chapter_list_selector` in config

### Issue 3: "Garbled text in extracted content"

**Cause**: Wrong character encoding detected

**Solution**: Specify encoding explicitly:

```json
{
  "encoding": "gb2312",  // Traditional Chinese variant
  "output_format": "txt"
}
```

Common encodings:
- UTF-8 (default, modern sites)
- GB2312 (legacy Simplified Chinese)
- Big5 (Traditional Chinese/Taiwan)
- Shift-JIS (Japanese sites)

---

## Testing Locally

Run unit tests:

```bash
cd novel-hub/backend

# Run all research-related tests
python -m pytest tests/test_research_parser.py tests/test_research_e2e.py -v

# With coverage metrics
pytest tests/ -v --cov=app --cov=workbench
```

Expected output:
```
test_research_parser.py::TestResearchParser::test_initialization PASSED
test_research_parser.py::TestResearchParser::test_parse_chapter_list_with_html PASSED
...

===================== 8 passed in 0.34s =====================
```

---

## Production Deployment Checklist

Before deploying to production environment:

- [ ] Set `ADMIN_API_TOKEN` environment variable
- [ ] Configure CORS origins (`ADMIN_CORS_ORIGINS`)
- [ ] Switch from SQLite to PostgreSQL for multi-user support
- [ ] Replace Redis queue system for background tasks
- [ ] Implement rotating proxy pool for anti-blocking
- [ ] Set up monitoring/logging service
- [ ] Test with real-world URLs under load

---

## API Reference

Full API specification available at: [`docs/contracts/research-api.md`](./docs/contracts/research-api.md)

Includes curl command examples, authentication requirements, and rate limiting policies.

---

## Contributing

When adding new research source rules:

1. ✅ Include detailed comments explaining selector rationale
2. ✅ Provide sample URLs for testing each source type
3. ✅ Update documentation with any new features added
4. ✅ Add unit tests covering edge cases (empty pages, timeouts, errors)

Example rule template:
```json
{
  "name": "TargetWebsiteName",
  "base_url": "https://target.website",
  "chapter_list_selector": "li.chapter-item > a[href]",
  "title_selector": ".chapter-title",
  "content_selector": ".chapter-content p",
  "pagination_selector": "a.next-page[rel='next']",
  "output_format": "txt",
  "encoding": "utf-8",
  "rate_limit": 1.0,
  "description": "Description of what this source provides",
  "tags": ["webnovel", "fantasy", "popular"]
}
```

---

## Next Steps (Roadmap)

Planned enhancements for future releases:

- [ ] WebSocket real-time progress updates (replace polling)
- [ ] Parallel task queue (scrape multiple sources concurrently)
- [ ] AI-powered content analysis (sentiment, style detection)
- [ ] Browser automation integration (Playwright/Puppeteer)
- [ ] Export preview viewer (PDF rendering in browser)

Current release focuses on core stability and correctness first.
