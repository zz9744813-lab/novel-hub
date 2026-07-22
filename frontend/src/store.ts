import { create } from "zustand";
import { api, Book } from "./api";

interface AppState {
  books: Book[];
  selectedBookId: string | null;
  loading: boolean;
  error: string | null;
  fetchBooks: () => Promise<void>;
  selectBook: (id: string) => void;
  createBook: (title: string, desc?: string) => Promise<string | null>;
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
      set({ books, loading: false });
      if (books.length > 0 && !get().selectedBookId) {
        set({ selectedBookId: books[0].book_id });
      }
    } catch (e: any) {
      set({ error: e.message, loading: false });
    }
  },
  selectBook: (id: string) => set({ selectedBookId: id }),
  createBook: async (title: string, desc?: string) => {
    try {
      const { book_id } = await api.books.create({ title, description: desc });
      await get().fetchBooks();
      set({ selectedBookId: book_id });
      return book_id;
    } catch (e: any) {
      set({ error: e.message });
      return null;
    }
  },
}));