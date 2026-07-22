STATUS_LABELS = {
    "idea": "灵感",
    "outline": "大纲",
    "draft": "草稿",
    "rewrite": "重写",
    "polish": "润色",
    "done": "完成",
    "published": "已发布",
}


def status_label(value: str) -> str:
    return STATUS_LABELS.get(value or "", value or "")


KIND_LABELS = {
    "character": "人物",
    "location": "地点",
    "item": "物品",
    "organization": "势力",
    "thread": "伏笔",
    "concept": "概念",
    "event": "事件",
}


def kind_label(value: str) -> str:
    return KIND_LABELS.get(value or "", value or "")
