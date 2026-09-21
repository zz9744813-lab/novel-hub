/// <reference types="vitest/globals" />
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, normalizeChapterRun, normalizeChapterRunList } from "../api";
import type { ChapterRunRecord } from "../api";

function mockFetchOnce(body: unknown, init: { status?: number; ok?: boolean } = {}) {
  const status = init.status ?? 200;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
  );
  vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
  return fetchMock;
}

describe("api.chapters.runsList", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
    window.localStorage.setItem("novelforge_admin_token", "test-token");
  });

  it("returns normalized typed runs on success (bare array response)", async () => {
    const fetchMock = mockFetchOnce([
      {
        run_id: "r-1",
        status: "succeeded",
        current_step: "finalize",
        control_requested: null,
        error_code: null,
        error_detail: null,
        started_at: "2026-09-21T01:00:00",
        finished_at: "2026-09-21T01:10:00",
      },
      {
        run_id: "r-2",
        status: "failed",
        current_step: "draft",
        control_requested: "pause",
        error_code: "PATCH_STALE",
        error_detail: { message: "stale patch applied" },
        started_at: "2026-09-21T02:00:00",
        finished_at: null,
      },
    ]);
    const result = await api.chapters.runsList("ch-123");

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, opts] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(String(url)).toContain("/api/chapters/ch-123/runs");
    expect(opts.headers).toMatchObject({ Authorization: "Bearer test-token" });

    expect(result.chapter_id).toBe("ch-123");
    expect(result.total).toBe(2);
    expect(result.runs[0]).toEqual<ChapterRunRecord>({
      run_id: "r-1",
      status: "succeeded",
      current_step: "finalize",
      control_requested: null,
      error_code: null,
      error_detail: null,
      started_at: "2026-09-21T01:00:00",
      finished_at: "2026-09-21T01:10:00",
    });
    // object error_detail preserved as unknown (not rendered directly)
    expect(result.runs[1].error_detail).toEqual({ message: "stale patch applied" });
  });

  it("models an empty list explicitly — resolves, total 0, not an error", async () => {
    mockFetchOnce([]);
    const result = await api.chapters.runsList("ch-empty");
    expect(result).toEqual({ chapter_id: "ch-empty", runs: [], total: 0 });
  });

  it("accepts a wrapped {runs: [...]} payload shape", async () => {
    mockFetchOnce({ runs: [{ run_id: "r-9", status: "running" }] });
    const result = await api.chapters.runsList("ch-wrapped");
    expect(result.total).toBe(1);
    expect(result.runs[0].status).toBe("running");
    expect(result.runs[0].current_step).toBeNull();
  });

  it("throws on failed HTTP request (does not resolve to empty)", async () => {
    mockFetchOnce({ detail: "boom" }, { status: 500 });
    await expect(api.chapters.runsList("ch-err")).rejects.toThrow(/500/);
  });

  it("throws on network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("network down"); }) as unknown as typeof fetch);
    await expect(api.chapters.runsList("ch-err")).rejects.toThrow("network down");
  });
});

describe("normalizeChapterRun boundary cases", () => {
  it("handles a row with all nested data missing", () => {
    const run = normalizeChapterRun("ch-x", {});
    expect(run).toEqual<ChapterRunRecord>({
      run_id: "",
      status: "unknown",
      current_step: null,
      control_requested: null,
      error_code: null,
      error_detail: null,
      started_at: null,
      finished_at: null,
    });
  });

  it("handles malformed field types without crashing", () => {
    const run = normalizeChapterRun("ch-x", {
      run_id: 42,
      status: { nested: true },
      current_step: ["a", "b"],
      error_detail: undefined,
      started_at: null,
    });
    expect(run.run_id).toBe("42");
    expect(run.status).toBe("unknown");
    expect(run.current_step).toBeNull();
    expect(run.error_detail).toBeNull();
  });

  it("drops null / non-object rows from a list payload", () => {
    const result = normalizeChapterRunList("ch-x", [null, "garbage", { run_id: "ok", status: "queued" }]);
    expect(result.total).toBe(1);
    expect(result.runs[0].run_id).toBe("ok");
  });

  it("treats a completely malformed payload as an explicit empty list", () => {
    expect(normalizeChapterRunList("ch-x", "not-json-object")).toEqual({
      chapter_id: "ch-x",
      runs: [],
      total: 0,
    });
    expect(normalizeChapterRunList("ch-x", null)).toEqual({
      chapter_id: "ch-x",
      runs: [],
      total: 0,
    });
  });
});

describe("backward compatibility", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("legacy api.chapters.runs still returns the raw typed array", async () => {
    mockFetchOnce([{ run_id: "r-1", status: "queued" }]);
    const runs = await api.chapters.runs("ch-legacy");
    expect(Array.isArray(runs)).toBe(true);
    expect(runs[0].run_id).toBe("r-1");
  });

  it("api.tasks.list still works unchanged", async () => {
    mockFetchOnce({ items: [], page: 1, page_size: 50, total: 0, pages: 0, task_types: [] });
    const data = await api.tasks.list({ page: 1 });
    expect(data.total).toBe(0);
  });
});
