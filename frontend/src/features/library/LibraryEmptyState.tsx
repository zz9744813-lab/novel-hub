import { BookPlus } from "lucide-react";

export function LibraryEmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="panel flex flex-col items-center py-20 text-center">
      <BookPlus size={32} className="text-text-disabled mb-3 opacity-40" />
      <p className="text-sm text-text-secondary" style={{ fontWeight: 510 }}>
        书架还是空的
      </p>
      <p className="text-xs text-text-tertiary mt-1 max-w-sm">
        从企划书创建小说（不会先建空项目），或创建空白小说后在作品首页继续写。
      </p>
      <button onClick={onNew} className="btn-primary mt-5 text-xs py-2 px-4">
        新建小说
      </button>
    </div>
  );
}
