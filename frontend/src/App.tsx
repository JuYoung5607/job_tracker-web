import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, Feed, FeedbackDecision, Job, SearchConditions, SourceStat, User } from "./api";

const statusLabels: Record<string, string> = {
  active: "모집 중", changed: "정보 변경", unknown: "확인 필요", review: "확인 필요"
};

function AuthPage({ onAuthenticated }: { onAuthenticated: (user: User) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const user = mode === "login" ? await api.login(email, password) : await api.register(email, password);
      onAuthenticated(user);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "로그인하지 못했습니다.");
    } finally { setBusy(false); }
  }

  return <main className="auth-shell">
    <section className="auth-intro">
      <a className="brand" href="#"><span>JC</span> Job Compass</a>
      <div className="intro-copy">
        <p className="eyebrow">검증된 채용 탐색</p>
        <h1>흩어진 공고를 모으고,<br /><em>당신의 기준</em>으로 정리합니다.</h1>
        <p>여러 출처의 핵심 정보만 비교하고, 지원은 언제나 원문에서 이어가세요.</p>
      </div>
      <div className="principles"><span>원문 중심</span><span>개인화</span><span>출처 투명성</span></div>
    </section>
    <section className="auth-panel">
      <form className="auth-card" onSubmit={submit}>
        <p className="eyebrow">{mode === "login" ? "다시 만나서 반가워요" : "나만의 채용 피드 시작하기"}</p>
        <h2>{mode === "login" ? "로그인" : "회원가입"}</h2>
        <label>이메일<input type="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="name@example.com" /></label>
        <label>비밀번호<input type="password" required minLength={10} value={password} onChange={e => setPassword(e.target.value)} placeholder="10자 이상 입력" /></label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? "처리 중…" : mode === "login" ? "로그인" : "계정 만들기"}</button>
        <button className="text-button" type="button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
          {mode === "login" ? "처음이신가요? 회원가입" : "이미 계정이 있나요? 로그인"}
        </button>
      </form>
    </section>
  </main>;
}

const splitValues = (value: string) => value.split(",").map(item => item.trim()).filter(Boolean);

function SearchSetup({ initial, onComplete }: {
  initial?: SearchConditions;
  onComplete: (conditions: SearchConditions, count: number) => void;
}) {
  const [jobs, setJobs] = useState((initial?.target_jobs ?? ["AI Engineer"]).join(", "));
  const [skills, setSkills] = useState((initial?.skills ?? []).join(", "));
  const [locations, setLocations] = useState((initial?.locations ?? ["서울", "경기"]).join(", "));
  const [exclusions, setExclusions] = useState((initial?.exclusion_conditions ?? []).join(", "));
  const [employment, setEmployment] = useState((initial?.employment_types ?? ["정규직"]).join(", "));
  const [levels, setLevels] = useState<SearchConditions["experience_levels"]>(
    initial?.experience_levels ?? ["entry", "junior"]
  );
  const [maxYears, setMaxYears] = useState(String(initial?.max_experience_years ?? 3));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function toggleLevel(level: SearchConditions["experience_levels"][number]) {
    setLevels(current => current.includes(level)
      ? current.filter(item => item !== level)
      : [...current, level]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    const conditions: SearchConditions = {
      target_jobs: splitValues(jobs), skills: splitValues(skills),
      exclusion_conditions: splitValues(exclusions), locations: splitValues(locations),
      experience_levels: levels, max_experience_years: maxYears ? Number(maxYears) : null,
      employment_types: splitValues(employment),
      preferred_source_domains: ["jobkorea.co.kr", "saramin.co.kr", "incruit.com", "jumpit.saramin.co.kr"]
    };
    try {
      const result = await api.saveSearchConditions(conditions);
      onComplete(result.conditions, result.visible_job_count);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "검색 조건을 저장하지 못했습니다.");
    } finally { setBusy(false); }
  }

  return <main className="setup-shell">
    <form className="setup-card" onSubmit={submit}>
      <a className="brand" href="#"><span>JC</span> Job Compass</a>
      <div><p className="eyebrow">개인화 기준 설정</p><h1>{initial ? "검색 조건 수정" : "어떤 기회를 찾고 있나요?"}</h1><p>명시한 조건은 강한 필터로 사용하고, 이후의 관심 표시는 정렬에만 반영합니다.</p></div>
      <div className="setup-grid">
        <label className="wide">희망 직무<input required value={jobs} onChange={e => setJobs(e.target.value)} placeholder="AI Engineer, Embedded AI" /><small>여러 개는 쉼표로 구분하세요.</small></label>
        <label>기술 키워드<input value={skills} onChange={e => setSkills(e.target.value)} placeholder="Python, FastAPI" /></label>
        <label>근무 지역<input value={locations} onChange={e => setLocations(e.target.value)} placeholder="서울, 경기" /></label>
        <fieldset><legend>경력 수준</legend><label><input type="checkbox" checked={levels.includes("entry")} onChange={() => toggleLevel("entry")} /> 신입</label><label><input type="checkbox" checked={levels.includes("junior")} onChange={() => toggleLevel("junior")} /> 주니어</label></fieldset>
        <label>최대 요구 경력<input type="number" min="0" max="50" value={maxYears} onChange={e => setMaxYears(e.target.value)} /><small>공고의 최소 요구 경력 상한입니다.</small></label>
        <label>고용 형태<input value={employment} onChange={e => setEmployment(e.target.value)} placeholder="정규직" /></label>
        <label>제외 키워드<input value={exclusions} onChange={e => setExclusions(e.target.value)} placeholder="파견, 상주 근무" /></label>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="primary" disabled={busy}>{busy ? "추천 피드 생성 중…" : "조건 저장하고 추천 보기"}</button>
    </form>
  </main>;
}

function JobCard({ job, onFeedback, onReport }: {
  job: Job;
  onFeedback: (job: Job, decision: FeedbackDecision) => void;
  onReport: (job: Job) => void;
}) {
  return <article className="job-card">
    <div className="card-top">
      <div><p className="company">{job.company_name}</p><h3>{job.job_title}</h3></div>
      <span className={`status ${job.screening_decision}`}>{job.screening_decision === "review" ? "검토 필요" : statusLabels[job.recruiting_status] ?? "모집 확인"}</span>
    </div>
    <div className="facts">
      <span>{job.location.join(" · ") || "지역 미확인"}</span>
      <span>{job.experience_level || "경력 미확인"}</span>
      <span>{job.employment_type || "고용형태 미확인"}</span>
    </div>
    <div className="reason-box"><strong>추천 이유</strong><p>{job.reasons.join(" · ") || "설정한 검색 범위에 포함된 공고입니다."}</p></div>
    {job.screening_decision === "review" && job.review_reasons.length > 0 &&
      <p className="review-note">확인할 점: {job.review_reasons.join(" · ")}</p>}
    <div className="card-footer">
      <div className="feedback-group" aria-label="공고 피드백">
        {(["interested", "hold", "not_interested"] as FeedbackDecision[]).map(value =>
          <button key={value} className={job.feedback_decision === value ? "selected" : ""} onClick={() => onFeedback(job, value)}>
            {value === "interested" ? "관심" : value === "hold" ? "보류" : "관심 없음"}
          </button>)}
      </div>
      <div className="source-actions"><button onClick={() => onReport(job)}>정보 제보</button><a href={job.source_url} target="_blank" rel="noreferrer">{job.source_name} 원문 ↗</a></div>
    </div>
  </article>;
}

function Dashboard({ user, onLogout, onEditConditions }: {
  user: User; onLogout: () => void; onEditConditions: () => void;
}) {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [stats, setStats] = useState<SourceStat[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [reportJob, setReportJob] = useState<Job | null>(null);
  const [reportType, setReportType] = useState("correction");
  const [message, setMessage] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    Promise.all([api.jobs(), api.sourceStats()]).then(([nextFeed, nextStats]) => {
      setFeed(nextFeed); setStats(nextStats);
    }).finally(() => setLoading(false));
  }, []);

  const jobs = useMemo(() => (feed?.items ?? []).filter(job =>
    `${job.company_name} ${job.job_title} ${job.location.join(" ")}`.toLowerCase().includes(query.toLowerCase())
  ), [feed, query]);

  async function feedback(job: Job, decision: FeedbackDecision) {
    await api.feedback(job.job_id, decision);
    setFeed(current => current ? { ...current, items: current.items.map(item => item.job_id === job.job_id ? { ...item, feedback_decision: decision } : item) } : current);
    setNotice("피드백을 저장했어요.");
  }

  async function submitReport(event: FormEvent) {
    event.preventDefault();
    if (!reportJob) return;
    await api.correction({ job_id: reportJob.job_id, source_url: reportJob.source_url, request_type: reportType, message });
    setReportJob(null); setMessage(""); setNotice("요청을 접수했어요. 확인 후 반영하겠습니다.");
  }

  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="#"><span>JC</span> Job Compass</a><div className="user-menu"><span>{user.email}</span><button onClick={onEditConditions}>검색 조건</button><button onClick={onLogout}>로그아웃</button></div></header>
    <main className="dashboard">
      <section className="dashboard-head">
        <div><p className="eyebrow">오늘의 맞춤 공고</p><h1>좋은 기회를 놓치지 않도록.</h1><p>검증 상태와 추천 이유를 확인한 뒤, 지원은 출처 원문에서 진행하세요.</p></div>
        <div className="summary"><strong>{feed?.items.length ?? 0}</strong><span>추천 공고</span><strong>{feed?.version ?? 0}</strong><span>피드 버전</span></div>
      </section>
      <section className="toolbar"><input aria-label="공고 검색" value={query} onChange={e => setQuery(e.target.value)} placeholder="회사, 직무, 지역 검색" /><span>{jobs.length}개의 결과</span></section>
      {notice && <button className="notice" onClick={() => setNotice("")}>{notice} ×</button>}
      <section className="feed" aria-live="polite">
        {loading ? <div className="empty">추천 피드를 불러오는 중입니다.</div> : jobs.length > 0 ? jobs.map(job =>
          <JobCard key={job.job_id} job={job} onFeedback={feedback} onReport={setReportJob} />
        ) : <div className="empty"><strong>아직 표시할 추천 공고가 없어요.</strong><p>수집·검증 파이프라인이 이 계정의 피드를 생성하면 이곳에 나타납니다.</p></div>}
      </section>
      <section className="transparency"><div><p className="eyebrow">출처 투명성</p><h2>어디에서 얼마나 확인했는지 기록합니다.</h2><p>접근 제한을 우회하지 않고, 상세 문단과 이미지는 재게시하지 않습니다.</p></div><div className="stats">{stats.length ? stats.map(stat => <div key={stat.source_name}><strong>{stat.source_name}</strong><span>요청 {stat.request_count}회 · 누적 {stat.accumulated_job_count}건</span></div>) : <p>아직 기록된 출처 통계가 없습니다.</p>}</div></section>
    </main>
    {reportJob && <div className="modal-backdrop" role="presentation"><form className="report-modal" onSubmit={submitReport}><button className="modal-close" type="button" onClick={() => setReportJob(null)}>×</button><p className="eyebrow">정보 제보</p><h2>{reportJob.company_name}</h2><label>요청 종류<select value={reportType} onChange={e => setReportType(e.target.value)}><option value="correction">정보 정정</option><option value="removal">삭제 요청</option></select></label><label>내용<textarea required minLength={5} value={message} onChange={e => setMessage(e.target.value)} placeholder="확인이 필요한 내용을 알려주세요." /></label><button className="primary">요청 접수</button></form></div>}
  </div>;
}

function SignedInApp({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [conditions, setConditions] = useState<SearchConditions | null | undefined>(undefined);
  const [editing, setEditing] = useState(false);
  useEffect(() => { api.searchConditions().then(setConditions); }, []);
  if (conditions === undefined) return <div className="boot">개인화 설정을 확인하고 있습니다.</div>;
  if (conditions === null || editing) return <SearchSetup initial={conditions ?? undefined}
    onComplete={(saved) => { setConditions(saved); setEditing(false); }} />;
  return <Dashboard user={user} onLogout={onLogout} onEditConditions={() => setEditing(true)} />;
}

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  useEffect(() => { api.me().then(setUser).catch(() => setUser(null)); }, []);
  if (user === undefined) return <div className="boot">Job Compass</div>;
  if (user === null) return <AuthPage onAuthenticated={setUser} />;
  return <SignedInApp user={user} onLogout={async () => { await api.logout(); setUser(null); }} />;
}
