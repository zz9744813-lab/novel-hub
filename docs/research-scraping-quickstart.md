# Research Scraping Feature - Quick Start Guide

This guide provides step-by-step instructions for setting up and using NovelForge's external research scraping functionality, which implements So Novel rule engine concepts in Python.

## Prerequisites

- Python 3.10+ 
- Node.js 18+ (for frontend)
- SQLite 3.x (bundled with Python)

## Dependencies Installation

### Backend Dependencies

```bash
cd novel-hub/backend

# Install Python dependencies
pip install httpx beautifulsoup4 lxml aiohttp pydantic fastapi uvicorn ebooklib reportlab

# Verify installation
python -c "import httpx; import bs4; print('✅ All dependencies installed')"
```

### Frontend Dependencies

```bash
cd novel-hub/frontend

# Install additional packages (if not already done)
npm install @xyflow/react

# Verify build works
npm run build
```

## Step 1: Import Existing Rules (Optional)

So Novel's rule configurations can be imported and converted:

```bash
cd novel-hub/backend

# Run the importer script
python -m scripts.import_sonovel_rules --dry-run

# Or save to actual configuration file
python -m scripts.import_sonovel_rules
```

This creates `data/research_sources.json` with sample rules.

## Step 2: Configure Research Sources

Edit `backend/data/research_sources.json` or add sources via API:

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
      "description": "Mainstream Chinese web novel platform"
    }
  ]
}
```

**Selector Tips:**

| Selector Type | Example | Purpose |
|---|---|---|
| CSS Class | `.chapter-title` | Match by class name |
| CSS ID | `#chapter-content` | Match by ID |
| Tag + Class | `div.content` | Combine tag and class |
| Attribute | `a[href*="/ch"]` | Match attribute pattern |

## Step 3: Start Backend Server

```bash
cd novel-hub/backend

# Launch FastAPI dev server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Verify endpoints are available:

```bash
curl http://localhost:8000/api/research/sources
# Should return list of configured sources
```

## Step 4: Start Frontend Application

```bash
cd novel-hub/frontend

# Launch Vite dev server
npm run dev
```

Navigate to `http://localhost:5173` and access the **"调研"** tab from sidebar.

## Step 5: Create Research Task

1. In ResearchPage UI, enter:
   - **Source Name**: Select from dropdown (e.g., "起点中文网示例")
   - **Target URL**: Full chapter listing page URL
   
2. Click **"开始调研"** button

3. Monitor progress in real-time:
   - Progress bar shows percentage completed
   - Chapter count updates as scraping advances
   - Error messages appear if extraction fails

## Step 6: Export Results

Once task status is "completed":

1. Open backend terminal where task was running
2. Navigate to export directory:
   ```bash
   cd novel-hub/backend/data/research_exports
   ls -l  # Should show exported files
   ```

3. Export formats generated:
   - `{task_id}_export.txt` - Plain text version
   - `{task_id}_export.epub` - Readable eBook format
   - `{task_id}_export.pdf` - A5 formatted document

## Common Issues & Solutions

### Issue 1: "Failed to fetch URL"

**Cause**: Target website blocked scraper's User-Agent

**Solution**: Add headers to request:

```python
# In workbench/collab/research_scraper.py
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
```

### Issue 2: "No chapters found"

**Cause**: Selectors don't match current page structure

**Solution**: Use browser DevTools to inspect and update selectors:

1. Right-click chapter link → Inspect Element
2. Copy CSS selector from highlighted element
3. Update `chapter_list_selector` in config

### Issue 3: "Garbled text in extracted content"

**Cause**: Wrong character encoding detected

**Solution**: Specify encoding explicitly:

```json
{
  "encoding": "gb2312",  // or "big5" for Traditional Chinese
  ...
}
```

## Testing Locally

Run unit tests to verify implementation:

```bash
cd novel-hub/tests

# Run parser tests
pytest test_research_parser.py -v

# Run integration tests  
pytest test_research_integration.py -v

# Run both with coverage
pytest tests/ -v --cov=app --cov=workbench
```

Expected output:

```
test_research_parser.py::TestResearchParser::test_initialization PASSED
test_research_parser.py::TestResearchParser::test_parse_chapter_list_with_html PASSED
...

===================== 8 passed in 0.34s =====================
```

## Next Steps

### Production Deployment

1. **Task Queue**: Replace in-memory queue with Redis/RabbitMQ
2. **Proxy Pool**: Add rotating proxy support for large-scale scraping
3. **Rate Limiting**: Implement global rate limiter across all sources
4. **Database Migration**: Switch from SQLite to PostgreSQL for multi-user support

### Extending to New Sources

For each new target website:

1. Visit target site and identify page structure
2. Extract CSS/XPath selectors using browser tools
3. Test selectors with Sample HTML
4. Add to `research_sources.json`
5. Validate with `import_sonovel_rules.py --dry-run`

## API Reference

Full API specification: [`docs/contracts/research-api.md`](./docs/contracts/research-api.md)

## Contributing

When adding new source rules:

- Include detailed comments explaining selector rationale
- Provide sample URLs for testing
- Update documentation with any new features
- Add unit tests covering edge cases
