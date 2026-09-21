/// <reference types="vitest/globals" />
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "../../../api";
import type { TaskItem, TaskListResponse } from "../../../api";
import { ChapterRunsPanel, INITIAL_RUNS_STATE, WritingTasksPage, loadChapterRuns } from "../WritingTasksPage";
import type { RunsState } from "../WritingTasksPage";

function chapterTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    task_id: "chapter:run-1",
    task_type: "chapter",
    entity_id: "run-1",
    book_id: "b-1",
    book_title: "测试书",
    chapter_id: "ch-1",
    chapter_no: 3,
    status: "succeeded",
    progress: null,
    current_step: "finalize",
    control_requested: null,
    error: null,
    actions: [],
    created_at: null,
    updated_at: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

function listResponse(items: TaskItem[]): TaskListResponse {
  return { items, page: 1, page_size: 50, total: items.length, pages: items.length ? 1 : 0, task_types: ["chapter"] };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("loadChapterRuns (page state logic)", () => {
  it("resolves to ready state on success", async () => {
    vi.spyOn(api.chapters, "runsList").mockResolvedValue({
      chapter_id: "ch-1",
      runs: [{ run_id: "r-1", status: "queued", current_step: null, control_requested: null, error_code: null, error_detail: null, started_at: null, finished_at: null }],
      total: 1,
    });
    const state = await loadChapterRuns("ch-1");
    expect(state.phase).toBe("ready");
    if (state.phase === "ready") expect(state.result.total).toBe(1);
  });

  it("resolves to ready with empty result — empty is NOT error", async () => {
    vi.spyOn(api.chapters, "runsList").mockResolvedValue({ chapter_id: "ch-1", runs: [], total: 0 });
    const state = await loadChapterRuns("ch-1");
    expect(state.phase).toBe("ready");
    if (state.phase === "ready") expect(state.result.runs).toEqual([]);
  });

  it("resolves to error state when the request fails", async () => {
    vi.spyOn(api.chapters, "runsList").mockRejectedValue(new Error("500: internal"));
    const state = await loadChapterRuns("ch-1");
    expect(state).toEqual({ phase: "error", message: "500: internal" });
  });

  it("coerces non-Error rejections into a string message", async () => {
    vi.spyOn(api.chapters, "runsList").mockRejectedValue("raw string failure");
    const state = await loadChapterRuns("ch-1");
    expect(state.phase).toBe("error");
    if (state.phase === "error") expect(state.message).toBe("raw string failure");
  });
});

describe("ChapterRunsPanel rendering", () => {
  it("shows a loading indicator in loading phase", () => {
    render(<ChapterRunsPanel state={{ phase: "loading" }} onRetry={() => {}} />);
    expect(screen.getByText(/加载运行历史/)).toBeTruthy();
  });

  it("shows an actionable error with retry — not the empty message", async () => {
    const onRetry = vi.fn();
    render(<ChapterRunsPanel state={{ phase: "error", message: "500: internal" }} onRetry={onRetry} />);
    expect(screen.getByText(/运行历史加载失败/)).toBeTruthy();
    expect(screen.getByText(/500: internal/)).toBeTruthy();
    expect(screen.queryByText(/暂无运行记录/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("shows the explicit empty message when the list is empty", () => {
    render(
      <ChapterRunsPanel
        state={{ phase: "ready", result: { chapter_id: "ch-1", runs: [], total: 0 } }}
        onRetry={() => {}}
      />
    );
    expect(screen.getByText(/该章节暂无运行记录/)).toBeTruthy();
  });

  it("renders malformed nested run data without crashing", () => {
    const state: RunsState = {
      phase: "ready",
      result: {
        chapter_id: "ch-1",
        runs: [
          // run with no run_id, object error_detail, null timestamps
          { run_id: "", status: "failed", current_step: null, control_requested: null, error_code: "PATCH_STALE", error_detail: { message: "stale" }, started_at: null, finished_at: null },
          { run_id: "r-2", status: "running", current_step: "draft", control_requested: null, error_code: null, error_detail: null, started_at: "2026-09-21T02:00:00", finished_at: null },
        ],
        total: 2,
      },
    };
    render(<ChapterRunsPanel state={state} onRetry={() => {}} />);
    expect(screen.getByTestId("runs-list")).toBeTruthy();
    expect(screen.getByText(/PATCH_STALE/)).toBeTruthy();
    expect(screen.getByText(/stale/)).toBeTruthy();
    // second run without started_at renders placeholder not "undefined"
    expect(screen.queryByText(/undefined/)).toBeNull();
  });

  it("initial state is idle and renders the loading placeholder (no crash)", () => {
    render(<ChapterRunsPanel state={INITIAL_RUNS_STATE} onRetry={() => {}} />);
    expect(screen.getByText(/加载运行历史/)).toBeTruthy();
  });
});

describe("WritingTasksPage integration", () => {
  it("renders chapter task rows and loads run history via the client on click", async () => {
    const user = userEvent.setup();
    vi.spyOn(api.tasks, "list").mockResolvedValue(listResponse([chapterTask()]));
    const runsList = vi.spyOn(api.chapters, "runsList").mockResolvedValue({
      chapter_id: "ch-1",
      runs: [{ run_id: "r-1", status: "succeeded", current_step: "finalize", control_requested: null, error_code: null, error_detail: null, started_at: "2026-09-21T01:00:00", finished_at: "2026-09-21T01:10:00" }],
      total: 1,
    });

    render(<WritingTasksPage />);
    await screen.findByText("第 3 章");

    await user.click(screen.getByTestId("runs-toggle-chapter:run-1"));
    await waitFor(() => expect(runsList).toHaveBeenCalledWith("ch-1"));
    await screen.findByTestId("runs-list");
    // "finalize" appears both in the task row and the run panel row
    expect(screen.getAllByText(/finalize/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/step: finalize/)).toBeTruthy();
  });

  it("surfaces a failed runs request as an error panel with retry", async () => {
    const user = userEvent.setup();
    vi.spyOn(api.tasks, "list").mockResolvedValue(listResponse([chapterTask()]));
    const runsList = vi
      .spyOn(api.chapters, "runsList")
      .mockRejectedValueOnce(new Error("502: bad gateway"))
      .mockResolvedValueOnce({ chapter_id: "ch-1", runs: [], total: 0 });

    render(<WritingTasksPage />);
    await screen.findByText("第 3 章");

    await user.click(screen.getByTestId("runs-toggle-chapter:run-1"));
    await screen.findByText(/运行历史加载失败：502/);
    expect(screen.queryByText(/该章节暂无运行记录/)).toBeNull();

    // retry recovers: error replaced by the (empty) ready state
    await user.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(runsList).toHaveBeenCalledTimes(2));
    await screen.findByText(/该章节暂无运行记录/);
  });

  it("shows the page-level empty state when the task list is empty", async () => {
    vi.spyOn(api.tasks, "list").mockResolvedValue(listResponse([]));
    render(<WritingTasksPage />);
    await screen.findByText(/暂无符合条件的任务/);
  });

  it("shows the page-level error when the task list request fails", async () => {
    vi.spyOn(api.tasks, "list").mockRejectedValue(new Error("503: down"));
    render(<WritingTasksPage />);
    await screen.findByText(/503: down/);
  });

  it("shows the loading state before data arrives", () => {
    vi.spyOn(api.tasks, "list").mockReturnValue(new Promise<TaskListResponse>(() => {}));
    render(<WritingTasksPage />);
    expect(screen.getByText(/加载任务队列/)).toBeTruthy();
  });
});
