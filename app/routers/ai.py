from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import NOVELS_ROOT, require_feature
from app.db import get_conn, get_setting, get_setting_decrypted
from app.security import require_auth
from app.services.ai_client import generate_ai_content, generate_ai_content_stream
from app.services.ai_context import build_context
from app.services.chapter_service import write_markdown
from app.services.library_service import list_chapters
from app.services.markdown_service import read_markdown, safe_slug
from app.services.path_service import chapter_path
from app.services.prompts_service import build_layered_prompt

router = APIRouter()


@router.post("/api/projects/{project}/ai/outline/volume")
async def ai_outline_volume(request: Request, project: str) -> Response:
    """T4.3 AI generate volume outline."""
    require_feature("ai")
    require_auth(request)
    data = await request.json()
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key: raise HTTPException(400, "AI not configured")

    context = build_context(project, None, "outline")
    base_prompt = f"基于以下项目上下文:\n{context}\n\n请为名为 '{data.get('slug')}' 的卷生成 3-5 段中文大纲。直接返回大纲正文,不要前后说明。"
    user_prompt = build_layered_prompt(get_setting, project, "outline", base_prompt)

    resp = await generate_ai_content(api_key, get_setting("ai_base_url"), get_setting("ai_model"), "你是一名资深小说大纲规划师,中文输出。", user_prompt)
    if resp:
        with get_conn() as conn:
            conn.execute("UPDATE volumes SET synopsis = ? WHERE project = ? AND slug = ?", (resp.strip(), project, data.get("slug")))
        return JSONResponse({"status": "ok", "synopsis": resp.strip()})
    return JSONResponse({"status": "error"})


@router.post("/api/projects/{project}/ai/outline/chapter")
async def ai_outline_chapter(request: Request, project: str) -> Response:
    """T4.3: AI splits a volume into N chapter outlines."""
    require_feature("ai")
    require_auth(request)
    data = await request.json()
    volume_slug = data.get("slug")
    num_chapters = int(data.get("count", 10))

    api_key = get_setting_decrypted("ai_api_key")
    if not api_key: raise HTTPException(400, "AI not configured")

    safe_project = safe_slug(project, fallback="project")

    context = build_context(safe_project, None, "outline")

    with get_conn() as conn:
        vol = conn.execute("SELECT * FROM volumes WHERE project=? AND slug=?", (safe_project, volume_slug)).fetchone()
        if not vol: raise HTTPException(404, "volume not found")

    base_prompt = f"""卷大纲:
{vol['synopsis']}

项目上下文:
{context}

请为该卷生成 {num_chapters} 个章节大纲。返回 JSON 数组,每项包含:title(字符串,中文),synopsis(字符串,2-3 句中文)。只返回合法 JSON,不要 markdown 代码块,不要任何前后说明。"""
    user_prompt = build_layered_prompt(get_setting, safe_project, "chapter_outline", base_prompt)
    response = await generate_ai_content(api_key, get_setting("ai_base_url"), get_setting("ai_model"), "你是一名章节大纲生成器。只输出合法 JSON。", user_prompt)

    if not response: return JSONResponse({"status": "error"})

    clean = response.strip()
    if clean.startswith("```json"): clean = clean[7:]
    if clean.startswith("```"): clean = clean[3:]
    if clean.endswith("```"): clean = clean[:-3]

    try:
        chapters_data = json.loads(clean.strip())
    except json.JSONDecodeError:
        return JSONResponse({"status": "error", "detail": "AI returned invalid JSON"})

    # Find current max chapter_int in this volume
    with get_conn() as conn:
        max_ch = conn.execute(
            "SELECT MAX(chapter_int) as m FROM file_index WHERE project=? AND volume=?",
            (safe_project, volume_slug)
        ).fetchone()
        start_idx = (max_ch["m"] or 0) + 1

    created = []
    for i, ch in enumerate(chapters_data):
        idx = start_idx + i
        title = ch.get("title", f"第 {idx} 章")
        synopsis = ch.get("synopsis", "")
        filename = f"{idx:05d}-{safe_slug(title, fallback=f'ch{idx}')}.md"

        new_path = NOVELS_ROOT / safe_project / "chapters" / volume_slug / filename
        new_path.parent.mkdir(parents=True, exist_ok=True)

        if new_path.exists(): continue  # don't overwrite

        fm = {
            "title": title,
            "chapter": str(idx),
            "status": "outline",
            "volume": volume_slug,
            "synopsis": synopsis,
        }
        write_markdown(new_path, fm, "")
        created.append({"filename": filename, "title": title})

    return JSONResponse({"status": "ok", "created": created})


@router.post("/api/projects/{project}/ai/outline/scene")
async def ai_outline_scene(request: Request, project: str) -> Response:
    """Split a chapter into scene H2 markers with summaries."""
    require_feature("ai")
    require_feature("scenes")
    require_auth(request)
    data = await request.json()
    chapter_filename = data.get("chapter")
    num_scenes = int(data.get("count", 4))

    api_key = get_setting_decrypted("ai_api_key")
    if not api_key: raise HTTPException(400, "AI not configured")

    safe_project = safe_slug(project, fallback="project")
    ch_path = chapter_path(safe_project, chapter_filename)
    if not ch_path.exists(): raise HTTPException(404, "chapter not found")

    fm, body = read_markdown(ch_path)

    context = build_context(safe_project, str(ch_path), "outline")

    prompt = f"""Chapter: {fm.get('title', '')}
Synopsis: {fm.get('synopsis', '')}

{context}

Generate {num_scenes} scenes for this chapter. Each scene should be a beat in the chapter. Return ONLY valid JSON array, each item: {{"title": "scene title", "summary": "2 sentence beat description"}}. No markdown fences."""

    response = await generate_ai_content(
        api_key, get_setting("ai_base_url"), get_setting("ai_model"),
        "You are a scene outliner. Return ONLY valid JSON.", prompt
    )
    if not response: return JSONResponse({"status": "error"})

    clean = response.strip()
    if clean.startswith("```json"): clean = clean[7:]
    if clean.startswith("```"): clean = clean[3:]
    if clean.endswith("```"): clean = clean[:-3]

    try:
        scenes_data = json.loads(clean.strip())
    except json.JSONDecodeError:
        return JSONResponse({"status": "error", "detail": "AI returned invalid JSON"})

    # Build new body: keep original prefix until first H2 (or original body if no H2),
    # then append H2 + summary blocks for each scene
    h2_pos = body.find("\n## ")
    prefix = body[:h2_pos] if h2_pos > 0 else body
    if prefix and not prefix.endswith("\n"): prefix += "\n"

    new_body = prefix
    for sc in scenes_data:
        new_body += f"\n## {sc.get('title', 'Scene')}\n\n*{sc.get('summary', '')}*\n\n"

    write_markdown(ch_path, fm, new_body, project=safe_project)
    return JSONResponse({"status": "ok", "scenes": scenes_data})


@router.post("/api/projects/{project}/ai/outline/draft")
async def ai_outline_draft(request: Request, project: str) -> Response:
    """Expand a scene's summary into prose draft."""
    require_feature("ai")
    require_feature("scenes")
    require_auth(request)
    data = await request.json()
    chapter_filename = data.get("chapter")
    scene_id = data.get("scene_id")

    api_key = get_setting_decrypted("ai_api_key")
    if not api_key: raise HTTPException(400, "AI not configured")

    safe_project = safe_slug(project, fallback="project")
    ch_path = chapter_path(safe_project, chapter_filename)
    if not ch_path.exists(): raise HTTPException(404, "chapter not found")

    with get_conn() as conn:
        scene = conn.execute(
            "SELECT * FROM scenes WHERE id=? AND project=?",
            (scene_id, safe_project)
        ).fetchone()
        if not scene: raise HTTPException(404, "scene not found")

    fm, body = read_markdown(ch_path)

    # Extract scene segment by char offsets
    start = scene["char_offset_start"] or 0
    end = scene["char_offset_end"] or len(body)
    scene_text = body[start:end]

    context = build_context(safe_project, str(ch_path), "draft")

    prompt = f"""{context}

Current scene to expand:
{scene_text}

Expand this scene into prose. Keep the existing H2 title and beat summary at top, but write 500-1000 words of actual narrative below. Match the project's style. Return only the expanded scene text (markdown OK)."""

    response = await generate_ai_content(
        api_key, get_setting("ai_base_url"), get_setting("ai_model"),
        "You are a creative novelist.", prompt
    )
    if not response: return JSONResponse({"status": "error"})

    new_body = body[:start] + response.strip() + "\n\n" + body[end:]
    write_markdown(ch_path, fm, new_body, project=safe_project)
    return JSONResponse({"status": "ok", "draft": response.strip()})


@router.get("/api/projects/{project}/ai/generate")
async def api_ai_generate(
    request: Request,
    project: str,
    mode: str = "continue",
    chapter: str = None,
    text: str = ""
) -> Response:
    require_feature("ai")
    require_auth(request)

    # Get settings
    api_key = get_setting_decrypted("ai_api_key")
    base_url = get_setting("ai_base_url")
    model = get_setting("ai_model")

    if not api_key:
        return JSONResponse({"status": "error", "detail": "AI API Key not set"}, status_code=400)

    # Build Context
    safe_project = safe_slug(project)
    ch_path = None
    if chapter:
        ch_path = str(chapter_path(safe_project, chapter))

    full_context = build_context(safe_project, ch_path, mode)

    system_prompt = "你是一名资深小说写作助手。中文输出。保持与上下文一致的人物性格、世界观、文风。"
    mode_instructions = {
        "continue": "请基于以下上下文,自然地续写接下来的几段。",
        "rewrite": "请润色用户给出的文本,保持原意,提升语感与节奏。",
        "check": "请检查用户给出的文本,找出与项目设定不一致或前后矛盾之处。直接列出。",
        "echo": "请基于上下文,建议本章如何呼应之前埋下的伏笔或设定。",
    }
    base_prompt = f"""项目上下文:
{full_context}

任务:{mode_instructions.get(mode, mode_instructions['continue'])}

用户提供的文本:
{text or '(无)'}
"""
    user_prompt = build_layered_prompt(get_setting, safe_project, "writing", base_prompt)

    async def event_generator():
        async for chunk in generate_ai_content_stream(api_key, base_url, model, system_prompt, user_prompt):
            yield f"data: {json.dumps({'content': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ===== Stage-specific AI generation =====

@router.post("/api/projects/{project}/stage/premise/ai")
async def api_ai_premise(request: Request, project: str) -> Response:
    require_feature("ai")
    require_auth(request)
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key:
        return JSONResponse({"status": "error", "detail": "AI 未配置,请先去设置页填 API Key"}, status_code=400)

    data = await request.json()
    action = data.get("action", "discuss")
    current = (data.get("current") or "").strip()
    safe_project = safe_slug(project, fallback="project")
    base_map = {
        "discuss": "我正在为一本长篇小说做立意。这是目前草稿。请用 3-5 句话反馈:最强点、最弱点、两个可以追问的问题。\n\n当前草稿:\n" + (current or "(空)"),
        "logline": "请基于以下方向,给我 3 个一句话简介(每个 30-60 字)。每个用不同切入角度,且要有钩子。\n\n方向:\n" + (current or "(空,你自己想 3 个有趣方向)"),
        "refs": "请推荐 5 部题材或氛围接近以下立意的作品(小说/影视),每部一行,格式:作品名 — 一句话说为什么相似。\n\n立意:\n" + (current or "(空)"),
        "critique": "请挑出以下立意的 3 个薄弱点。直接说,不要夸奖。如果立意为空,请说\"目前还没东西可以挑\"。\n\n立意:\n" + (current or "(空)"),
    }
    user_prompt = build_layered_prompt(get_setting, safe_project, "premise", base_map.get(action, base_map["discuss"]))
    text = await generate_ai_content(
        api_key,
        get_setting("ai_base_url", "https://api.openai.com/v1"),
        get_setting("ai_model", "gpt-3.5-turbo"),
        "你是一名资深小说编辑,中文输出,简洁直接,不要客套。",
        user_prompt,
    )
    if not text:
        return JSONResponse({"status": "error", "detail": "AI 没有返回内容"}, status_code=502)
    return JSONResponse({"status": "ok", "text": text})


@router.post("/api/projects/{project}/stage/worldview/ai")
async def api_ai_worldview(request: Request, project: str) -> Response:
    require_feature("ai")
    require_auth(request)
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key:
        return JSONResponse({"status": "error", "detail": "AI 未配置"}, status_code=400)

    data = await request.json()
    action = data.get("action", "extend")
    current = (data.get("current") or "").strip()
    safe_project = safe_slug(project, fallback="project")
    premise_path = NOVELS_ROOT / safe_project / ".workflow" / "premise.md"
    premise = premise_path.read_text(encoding="utf-8") if premise_path.exists() else ""
    head = f"立意参考:\n{premise[:800]}\n\n当前世界观稿:\n{current or '(空)'}\n\n"
    base_map = {
        "extend": head + "请帮我把世界观骨架填补完整。建议补:时代地理、核心规则、主要势力、视觉氛围。每节 2-4 句即可,具体不空泛。",
        "inconsistency": head + "请找出 3 处设定中的潜在漏洞或自相矛盾。直接列出,不要绕弯。",
        "names": head + "请基于上述世界观,起 8 个地名或势力名。每行一个,格式:名字 — 一句话用途。",
        "timeline": head + "请基于上述世界观,列出 5-8 个对故事至关重要的时间节点。每行一个,格式:[时间] 事件 — 影响。",
    }
    user_prompt = build_layered_prompt(get_setting, safe_project, "worldview", base_map.get(action, base_map["extend"]))
    text = await generate_ai_content(
        api_key,
        get_setting("ai_base_url", "https://api.openai.com/v1"),
        get_setting("ai_model", "gpt-3.5-turbo"),
        "你是一名资深小说世界观顾问。中文输出。具体,不空泛。",
        user_prompt,
    )
    if not text:
        return JSONResponse({"status": "error", "detail": "AI 没有返回"}, status_code=502)
    return JSONResponse({"status": "ok", "text": text})


@router.post("/api/projects/{project}/stage/characters/ai")
async def api_ai_characters(request: Request, project: str) -> Response:
    require_feature("ai")
    require_auth(request)
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key:
        return JSONResponse({"status": "error", "detail": "AI 未配置"}, status_code=400)

    data = await request.json()
    action = data.get("action", "cast")
    safe_project = safe_slug(project, fallback="project")
    premise = NOVELS_ROOT / safe_project / ".workflow" / "premise.md"
    worldview = NOVELS_ROOT / safe_project / ".workflow" / "worldview.md"
    premise_text = premise.read_text(encoding="utf-8") if premise.exists() else ""
    worldview_text = worldview.read_text(encoding="utf-8") if worldview.exists() else ""
    head = f"立意:\n{premise_text[:600]}\n\n世界观:\n{worldview_text[:800]}\n\n"
    base_map = {
        "cast": head + "请基于上面的设定,给我 3 个候选主角。每个用 4-6 行写:姓名、年龄、职业、核心动机、致命缺陷、出场动作画面。",
        "antagonist": head + "请配一个反派或对手。要求:动机能站得住脚,与主角形成镜像或互补。4-6 行。",
        "relations": head + "请基于设定,提议一个 5-7 人的人物关系网,并指出 1 个最有戏剧张力的对子。",
        "flaw": head + "请给主角一个致命缺陷(不是优点的反面),要能驱动后期反转。给 3 个候选,每个 2-3 行。",
    }
    user_prompt = build_layered_prompt(get_setting, safe_project, "characters", base_map.get(action, base_map["cast"]))
    text = await generate_ai_content(
        api_key,
        get_setting("ai_base_url", "https://api.openai.com/v1"),
        get_setting("ai_model", "gpt-3.5-turbo"),
        "你是一名资深小说人物顾问。中文输出。具体,可视化,不空泛。",
        user_prompt,
    )
    if not text:
        return JSONResponse({"status": "error", "detail": "AI 没有返回"}, status_code=502)
    return JSONResponse({"status": "ok", "text": text})


@router.post("/api/projects/{project}/stage/chapter_outline/ai")
async def api_ai_chapter_outline_one(request: Request, project: str) -> Response:
    """Generate a synopsis for a single chapter."""
    require_feature("ai")
    require_auth(request)
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key:
        return JSONResponse({"status": "error", "detail": "AI 未配置"}, status_code=400)

    data = await request.json()
    filename = data.get("filename")
    if not filename:
        raise HTTPException(400, "missing filename")
    safe_project = safe_slug(project, fallback="project")
    path = chapter_path(safe_project, filename)
    if not path.exists():
        raise HTTPException(404)

    fm, body = read_markdown(path)
    chapters = list_chapters(safe_project, sync=False)
    idx = next((i for i, c in enumerate(chapters) if c["filename"] == filename), -1)
    prev_syn = chapters[idx - 1].get("synopsis", "") if idx > 0 else ""
    next_syn = chapters[idx + 1].get("synopsis", "") if idx >= 0 and idx + 1 < len(chapters) else ""
    base_prompt = f"""请为这一章生成 1-3 句中文梗概,用于做细纲。直接返回梗概,不要前后说明。
本章号:{fm.get('chapter', '')}
本章标题:{fm.get('title', '')}
所在卷:{fm.get('volume', '')}
上一章梗概:{prev_syn or '(无)'}
下一章梗概:{next_syn or '(无)'}
本章已写正文(摘要):{(body or '')[:500]}
"""
    user_prompt = build_layered_prompt(get_setting, safe_project, "chapter_outline", base_prompt)
    text = await generate_ai_content(
        api_key,
        get_setting("ai_base_url", "https://api.openai.com/v1"),
        get_setting("ai_model", "gpt-3.5-turbo"),
        "你是一名章节大纲规划师。中文输出。1-3 句话,具体不空泛。",
        user_prompt,
    )
    if not text:
        return JSONResponse({"status": "error", "detail": "AI 没有返回"}, status_code=502)
    return JSONResponse({"status": "ok", "text": text.strip()})
