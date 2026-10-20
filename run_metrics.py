"""그래프 실행 결과를 단계별로 집계합니다. 판정 로직은 변경하지 않습니다."""

from collections import Counter


class RunMetrics:
    def __init__(self) -> None:
        self.search_candidate_ids: set[str] = set()
        self.processed_candidate_ids: set[str] = set()
        self.access_success = 0
        self.access_failures: Counter[str] = Counter()
        self.detail_pages = 0
        self.listings_with_links = 0
        self.unclassified_pages = 0
        self.extracted_pages = 0
        self.decisions: Counter[str] = Counter()
        self.lifecycle_statuses: Counter[str] = Counter()
        self.persisted_job_ids: set[str] = set()

    def observe(self, node_name: str, update: dict) -> None:
        if node_name in {"searcher_node", "listing_searcher_node", "jumpit_searcher_node"}:
            self.search_candidate_ids.update(
                candidate.candidate_id for candidate in update.get("candidates", [])
            )

        elif node_name == "candidate_iterator_node":
            current = update.get("current_candidate")
            if current is not None:
                self.processed_candidate_ids.add(current.candidate_id)

        elif node_name == "navigator_node":
            errors = update.get("errors", [])
            if any(error.error_type == "UnclassifiedPage" for error in errors):
                self.access_success += 1
                self.unclassified_pages += 1
            elif errors:
                self.access_failures.update(error.error_type for error in errors)
            else:
                self.access_success += 1
                if update.get("job_document") is not None:
                    self.detail_pages += 1
                elif update.get("discovered_candidates"):
                    self.listings_with_links += 1

        elif node_name == "extractor_node":
            if update.get("extracted_facts") is not None:
                self.extracted_pages += 1

        result = update.get("screening_result")
        if result is not None:
            self.decisions[str(result.decision)] += 1
        lifecycle = update.get("lifecycle_result")
        if lifecycle is not None:
            self.lifecycle_statuses[lifecycle.next_status.value] += 1
        if node_name == "job_persister_node" and update.get("persisted_job_id"):
            self.persisted_job_ids.add(update["persisted_job_id"])

    def print_summary(self) -> None:
        discovered_later = len(
            self.processed_candidate_ids - self.search_candidate_ids
        )
        print("\n===== 단계별 처리 요약 =====")
        print(f"검색 후보: {len(self.search_candidate_ids)}건")
        print(
            f"처리 후보: {len(self.processed_candidate_ids)}건 "
            f"(목록 등에서 추가 발견 {discovered_later}건)"
        )
        print(f"페이지 접근 성공: {self.access_success}건")
        print(f"페이지 접근 실패: {self.access_failures.total()}건")
        print(f"개별 공고로 분류: {self.detail_pages}건")
        print(f"목록에서 상세 링크 발견: {self.listings_with_links}건")
        print(f"페이지 유형 불명: {self.unclassified_pages}건")
        print(f"본문 추출 성공: {self.extracted_pages}건")
        print(
            "판정: "
            f"eligible {self.decisions['eligible']}건 / "
            f"excluded {self.decisions['excluded']}건 / "
            f"review {self.decisions['review']}건"
        )
        if self.lifecycle_statuses:
            details = " / ".join(
                f"{status} {self.lifecycle_statuses[status]}건"
                for status in ("active", "changed", "closed", "unavailable", "unknown")
            )
            print(f"공고 상태: {details}")
        print(f"저장/기존 기록 확인: {len(self.persisted_job_ids)}건")
        if self.access_failures:
            details = ", ".join(
                f"{name} {count}건"
                for name, count in self.access_failures.most_common()
            )
            print(f"접근 실패 유형: {details}")
