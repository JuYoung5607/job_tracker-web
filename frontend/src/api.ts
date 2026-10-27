export type User = { user_id: string; email: string };

export type FeedbackDecision = "interested" | "hold" | "not_interested";

export type Job = {
  job_id: string;
  company_name: string;
  job_title: string;
  source_url: string;
  source_name: string;
  location: string[];
  experience_level: string | null;
  employment_type: string | null;
  recruiting_status: string;
  opened_at: string | null;
  closes_at: string | null;
  is_new: boolean;
  score: number;
  reasons: string[];
  screening_decision: "eligible" | "review";
  review_reasons: string[];
  feedback_decision: FeedbackDecision | null;
};

export type Feed = { version: number; generated_at: string | null; items: Job[] };

export type SearchRun = {
  run_id: string;
  status: "queued" | "running" | "completed" | "failed" | "interrupted";
  phase: string;
  discovered_count: number;
  processed_count: number;
  eligible_count: number;
  review_count: number;
  excluded_count: number;
  failed_count: number;
  visible_count: number | null;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
};

export type SearchQuota = {
  daily_limit: number;
  remaining: number;
  next_available_at: string | null;
  timezone: "Asia/Seoul";
};

export type SourceStat = {
  source_name: string;
  request_count: number;
  accumulated_job_count: number;
  last_collected_at: string | null;
};

export type SearchConditions = {
  target_jobs: string[];
  skills: string[];
  exclusion_conditions: string[];
  locations: string[];
  experience_levels: ("entry" | "junior" | "mid" | "senior")[];
  max_experience_years: number | null;
  employment_types: string[];
  preferred_source_domains: string[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "요청을 처리하지 못했습니다.");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  me: () => request<User>("/api/auth/me"),
  register: (email: string, password: string) =>
    request<User>("/api/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),
  login: (email: string, password: string) =>
    request<User>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  jobs: () => request<Feed>("/api/jobs"),
  startSearch: () => request<SearchRun>("/api/search-runs", { method: "POST" }),
  latestSearch: () => request<{ run: SearchRun | null }>("/api/search-runs/latest"),
  searchQuota: () => request<SearchQuota>("/api/search-runs/quota"),
  sourceStats: () => request<SourceStat[]>("/api/source-stats"),
  searchConditions: async () => {
    const response = await fetch("/api/me/search-conditions", { credentials: "include" });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error("검색 조건을 불러오지 못했습니다.");
    return response.json() as Promise<SearchConditions>;
  },
  saveSearchConditions: (conditions: SearchConditions) =>
    request<{ conditions: SearchConditions; visible_job_count: number }>(
      "/api/me/search-conditions", { method: "PUT", body: JSON.stringify(conditions) }
    ),
  feedback: (jobId: string, decision: FeedbackDecision, reasons: string[]) =>
    request<{ preference_profile_version: number; visible_job_count: number }>(`/api/jobs/${jobId}/feedback`, {
      method: "PUT",
      body: JSON.stringify({ decision, reasons, comment: null })
    }),
  correction: (payload: { job_id: string; source_url: string; request_type: string; message: string }) =>
    request("/api/correction-requests", { method: "POST", body: JSON.stringify(payload) })
};
