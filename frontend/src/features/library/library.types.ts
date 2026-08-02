export interface BookshelfBook {
  book_id: string;
  title: string;
  subtitle?: string | null;
  cover_url?: string | null;
  cover_style?: { background: string; accent: string; bg: string };
  cover_generated?: boolean;
  tags: string[];
  lifecycle_status:
    | "draft"
    | "importing"
    | "writing"
    | "paused"
    | "needs_human"
    | "completed"
    | "archived"
    | string;
  finalized_chapters: number;
  planned_chapters: number | null;
  finalized_words: number;
  current_chapter_no: number | null;
  active_task?: {
    type: string;
    label: string;
    progress: number | null;
  } | null;
  unresolved_risk_count: number;
  updated_at: string | null;
  genre?: string | null;
  logline?: string | null;
}

export const LIFECYCLE_LABEL: Record<string, string> = {
  draft: "草稿",
  importing: "导入中",
  writing: "写作中",
  paused: "已暂停",
  needs_human: "待处理",
  completed: "已完结",
  archived: "已归档",
};
