import { create } from "zustand";
import { api, Book } from "./api";

interface AppState {
  books: Book[];
  selectedBookId: string | null;
  loading: boolean;
  error: string | null;
  fetchBooks: () => Promise<void>;
  selectBook: (id: string | null) => void;
  createBook: (title: string, desc?: string, targetChapters?: number) => Promise<string | null>;
}

export const useStore = create<AppState>((set, get) => ({
  books: [],
  selectedBookId: null,
  loading: false,
  error: null,
  fetchBooks: async () => {
    set({ loading: true, error: null });
    try {
      const books = await api.books.list();
      const normalized = (books || []).map((b: any) => ({
        ...b,
        book_id: b.book_id || b.id,
        title: b.title || "(未命名)",
        finalized_chapters: b.finalized_chapters ?? 0,
        finalized_words: b.finalized_words ?? 0,
      }));
      set({ books: normalized, loading: false });
      // Do NOT auto-select first book — keeps 项目总览 list visible and intentional
      const cur = get().selectedBookId;
      if (cur && !normalized.some((b) => b.book_id === cur)) {
        set({ selectedBookId: null });
      }
    } catch (e: any) {
      set({ error: e.message, loading: false });
    }
  },
  selectBook: (id: string | null) => set({ selectedBookId: id }),
  createBook: async (title: string, desc?: string, targetChapters?: number) => {
    try {
      const { book_id } = await api.books.create({
        title,
        description: desc,
        target_chapters: targetChapters ?? 500,
      });
      await get().fetchBooks();
      set({ selectedBookId: book_id });
      return book_id;
    } catch (e: any) {
      set({ error: e.message });
      return null;
    }
  },
}));
