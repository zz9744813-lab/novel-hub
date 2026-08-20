import { BookPlus, Plus } from "lucide-react";

export function LibraryEmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="panel rounded-card flex flex-col items-center py-20 text-center">
      <div className="mb-4 grid h-14 w-14 place-items-center rounded-card bg-brand-muted">
        <BookPlus size={24} className="text-brand-accent" />
      </div>
      <p className="text-emphasis text-text-primary" style={{ fontWeight: 510 }}>
        书架还是空的
      </p>
      <p className="text-body text-text-tertiary mt-1 max-w-sm">
        从企划书创建小说（不会先建空项目），或创建空白小说后在作品首页继续写。
      </p>
      <button onClick={onNew} className="btn-primary mt-5 text-body rounded-control py-2 px-4">
        <Plus size={14} /> 新建小说
      </button>
    </div>
  );
}
