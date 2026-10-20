"""사람인 검색 카드와 공고 본문 URL만 다루는 사이트별 어댑터."""

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from nodes.navigator_node import FetchedPage, PageLink
from nodes.page_fetcher import _check_public_host
from schemas.search import ExperienceLevel, SearchConditions


MAX_LIST_HTML_BYTES = 3_000_000
SARAMIN_HOST = "www.saramin.co.kr"


def saramin_detail_id(url: str, *, content: bool = False) -> str | None:
    parts = urlsplit(url)
    expected_path = (
        "/api/jobs/job/content" if content else "/zf_user/jobs/view"
    )
    if (parts.scheme != "https" or parts.hostname != SARAMIN_HOST
            or parts.port not in (None, 443) or parts.username or parts.password
            or parts.path != expected_path):
        return None
    values = parse_qs(parts.query).get("rec_idx", [])
    return values[0] if len(values) == 1 and values[0].isdigit() else None


def saramin_detail_url(list_url: str, href: str) -> str | None:
    """검색 카드의 상세 링크만 정규화합니다. 지원 버튼 링크는 제외합니다."""
    from urllib.parse import urljoin

    parts = urlsplit(urljoin(list_url, href))
    if (parts.scheme != "https" or parts.hostname != SARAMIN_HOST
            or parts.port not in (None, 443) or parts.username or parts.password
            or parts.path != "/zf_user/jobs/relay/view"):
        return None
    params = parse_qs(parts.query)
    if params.get("view_type") != ["search"]:
        return None
    values = params.get("rec_idx", [])
    if len(values) != 1 or not values[0].isdigit():
        return None
    return f"https://{SARAMIN_HOST}/zf_user/jobs/view?rec_idx={values[0]}"


def saramin_content_url(detail_url: str) -> str | None:
    rec_idx = saramin_detail_id(detail_url)
    if rec_idx is None:
        return None
    return f"https://{SARAMIN_HOST}/api/jobs/job/content?rec_idx={rec_idx}"


class SaraminListingParser(HTMLParser):
    """item_recruit 안의 주 공고 제목과 경력 표시만 묶습니다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[PageLink] = []
        self._card_depth = 0
        self._condition_depth = 0
        self._card_id: str | None = None
        self._title_heading = False
        self._title_href: str | None = None
        self._title_parts: list[str] = []
        self._condition_parts: list[str] = []
        self._capture_title = False
        self._ignore_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag in {"script", "style", "noscript"}:
            self._ignore_depth += 1
            return
        if self._ignore_depth:
            return
        if tag == "div":
            if self._card_depth:
                self._card_depth += 1
            elif "item_recruit" in classes:
                self._card_depth = 1
                self._card_id = attributes.get("value")
                self._title_href = None
                self._title_parts = []
                self._condition_parts = []
            if self._condition_depth:
                self._condition_depth += 1
            elif self._card_depth and "job_condition" in classes:
                self._condition_depth = 1
        elif self._card_depth and tag == "h2" and "job_tit" in classes:
            self._title_heading = True
        elif self._title_heading and tag == "a" and attributes.get("href"):
            self._title_href = attributes["href"]
            self._capture_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignore_depth:
            self._ignore_depth -= 1
            return
        if self._ignore_depth:
            return
        if tag == "a":
            self._capture_title = False
        elif tag == "h2":
            self._title_heading = False
        elif tag == "div":
            if self._condition_depth:
                self._condition_depth -= 1
            if self._card_depth:
                self._card_depth -= 1
                if self._card_depth == 0:
                    title = " ".join(self._title_parts).strip()
                    if self._title_href and title and self._card_id:
                        href_params = parse_qs(urlsplit(self._title_href).query)
                        if href_params.get("rec_idx") == [self._card_id]:
                            self.links.append(PageLink(
                                url=self._title_href,
                                text=title,
                                context=" ".join(self._condition_parts).strip(),
                            ))
                    self._card_id = None

    def handle_data(self, data: str) -> None:
        if self._ignore_depth:
            return
        value = data.strip()
        if not value:
            return
        if self._capture_title:
            self._title_parts.append(value)
        if self._condition_depth:
            self._condition_parts.append(value)


def fetch_saramin_listing_page(url: str) -> FetchedPage:
    """목록은 2MB를 조금 넘으므로 이 경로에만 3MB 상한을 적용합니다."""
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != SARAMIN_HOST
            or parts.path != "/zf_user/search/recruit"
            or parts.port not in (None, 443) or parts.username or parts.password):
        raise ValueError("사람인 검색 목록 URL이 아닙니다.")
    params = parse_qs(parts.query)
    if len(params.get("searchword", [])) != 1:
        raise ValueError("사람인 검색어가 없습니다.")
    _check_public_host(url)
    with httpx.Client(timeout=15.0, follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", url, headers={"User-Agent": "JobTracker/0.1"}) as response:
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "").lower():
                raise ValueError("사람인 검색 목록이 HTML이 아닙니다.")
            chunks = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_LIST_HTML_BYTES:
                    raise ValueError("사람인 검색 목록 크기 상한을 넘었습니다.")
                chunks.append(chunk)
            html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
            final_url = str(response.url)
            status = response.status_code
    parser = SaraminListingParser()
    parser.feed(html)
    return FetchedPage(url=final_url, title="", content="", links=parser.links, http_status=status)


def saramin_search_url(query: str, page: int, conditions: SearchConditions) -> str:
    params: dict[str, str | int] = {"searchword": query, "recruitPage": page}
    levels = set(conditions.experience_levels)
    if levels and levels.issubset({ExperienceLevel.ENTRY, ExperienceLevel.JUNIOR}):
        codes = []
        if ExperienceLevel.ENTRY in levels:
            codes.append("1")
        if ExperienceLevel.JUNIOR in levels:
            codes.append("2")
        params.update({"exp_cd": ",".join(codes), "exp_none": "y"})
        if ExperienceLevel.JUNIOR in levels and conditions.max_experience_years is not None:
            params["exp_max"] = conditions.max_experience_years
    return f"https://{SARAMIN_HOST}/zf_user/search/recruit?{urlencode(params)}"
