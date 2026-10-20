import ipaddress
import json
import re
import socket
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from nodes.navigator_node import FetchedPage, PageLink


MAX_HTML_BYTES = 2_000_000
MAX_TEXT_CHARS = 100_000
MAX_REDIRECTS = 3


def _contains_job_posting(value: object) -> bool:
    """JSON-LD 안에 JobPosting 타입이 있는지 확인합니다."""
    if isinstance(value, list):
        return any(_contains_job_posting(item) for item in value)

    if not isinstance(value, dict):
        return False

    item_type = value.get("@type")
    if item_type == "JobPosting":
        return True
    if isinstance(item_type, list) and "JobPosting" in item_type:
        return True

    return any(_contains_job_posting(item) for item in value.values())


def _job_postings(value: object) -> list[dict]:
    if isinstance(value, list):
        return [posting for item in value for posting in _job_postings(item)]
    if not isinstance(value, dict):
        return []
    item_type = value.get("@type")
    found = [value] if item_type == "JobPosting" or (
        isinstance(item_type, list) and "JobPosting" in item_type
    ) else []
    for item in value.values():
        found.extend(_job_postings(item))
    return found


def _structured_deadline(raw_json_parts: list[str]) -> str | None:
    """단일 공고의 JSON-LD 마감일만 원문 근거로 사용합니다."""
    postings = []
    for raw in raw_json_parts:
        try:
            postings.extend(_job_postings(json.loads(raw)))
        except json.JSONDecodeError:
            continue
    if len(postings) != 1:
        return None
    value = postings[0].get("validThrough")
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return f"구조화 데이터 공고 마감일: {value}"


def _jobkorea_embedded_detail_url(parent_url: str, html: str) -> str | None:
    parts = urlsplit(parent_url)
    if parts.hostname not in {"www.jobkorea.co.kr", "jobkorea.co.kr"}:
        return None
    match = re.fullmatch(r"/Recruit/GI_Read/(\d+)", parts.path, re.IGNORECASE)
    if not match:
        return None
    job_id = match.group(1)
    if not re.search(r"/Recruit/GI_Read_Comt_Ifrm\?Gno=" + job_id + r"(?!\d)", html, re.IGNORECASE):
        return None
    return f"https://www.jobkorea.co.kr/Recruit/GI_Read_Comt_Ifrm?Gno={job_id}"


def _saramin_open_status(url: str, title: str) -> str | None:
    parts = urlsplit(url)
    if parts.hostname != "www.saramin.co.kr" or parts.path != "/zf_user/jobs/view":
        return None
    match = re.search(r"\((D-\d+|오늘\s*마감|채용시\s*마감)\)", title)
    return match.group(1) if match else None


class JobPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.links: list[PageLink] = []
        self.iframe_sources: list[str] = []
        self.application_controls: list[str] = []
        self.active_application_evidence: str | None = None
        self._apply_area_depth = 0
        self._apply_area_badge: list[str] = []
        self._apply_area_button: list[str] = []
        self._capture_apply_badge = False
        self._capture_apply_button = False
        self.json_ld_parts: list[str] = []
        self._json_ld_buffer: list[str] = []

        self._in_title = False
        self._in_heading = False
        self._in_json_ld = False
        self._current_link: str | None = None
        self._link_text: list[str] = []
        self._button_text: list[str] | None = None
        self._ignore_depth = 0
        self._listing_row_depth = 0
        self._listing_row_text_start = 0
        self._listing_row_link_start = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)

        classes = (attributes.get("class") or "").split()
        if tag == "div" and "btnUpToIcon" in classes:
            self._apply_area_depth = 1
            self._apply_area_badge = []
            self._apply_area_button = []
        elif tag == "div" and self._apply_area_depth:
            self._apply_area_depth += 1
        if self._apply_area_depth and tag == "strong" and {"dday", "ing"}.issubset(classes):
            self._capture_apply_badge = True
        if self._apply_area_depth and tag == "button" and "btn_jobapp" in classes:
            self._capture_apply_button = True

        if tag == "iframe" and attributes.get("src"):
            self.iframe_sources.append(attributes["src"])

        if tag == "ul":
            if self._listing_row_depth:
                self._listing_row_depth += 1
            elif "c_row" in (attributes.get("class") or "").split():
                self._listing_row_depth = 1
                self._listing_row_text_start = len(self.text_parts)
                self._listing_row_link_start = len(self.links)

        if tag in {"style", "noscript"}:
            self._ignore_depth += 1
        elif tag == "script":
            if attributes.get("type", "").lower() == "application/ld+json":
                self._in_json_ld = True
                self._json_ld_buffer = []
            else:
                self._ignore_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_heading = True
            self.heading_parts.append("")
        elif tag == "a":
            self._current_link = attributes.get("href")
            self._link_text = []
        elif tag == "button":
            self._button_text = []
            self.application_controls.extend(
                value for key, value in attributes.items()
                if key in {"aria-label", "title"} and value
            )
        elif tag == "input" and attributes.get("type", "").lower() in {"submit", "button"}:
            self.application_controls.append(attributes.get("value") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "strong":
            self._capture_apply_badge = False
        elif tag == "button":
            self._capture_apply_button = False
        elif tag == "div" and self._apply_area_depth:
            self._apply_area_depth -= 1
            if self._apply_area_depth == 0:
                if (" ".join(self._apply_area_badge).strip() == "채용시"
                        and "홈페이지 지원" in " ".join(self._apply_area_button)):
                    self.active_application_evidence = "채용시"

        if tag == "ul" and self._listing_row_depth:
            self._listing_row_depth -= 1
            if self._listing_row_depth == 0:
                context = " ".join(self.text_parts[self._listing_row_text_start:])
                for link in self.links[self._listing_row_link_start:]:
                    link.context = context
        if tag in {"style", "noscript"} and self._ignore_depth:
            self._ignore_depth -= 1
        elif tag == "script":
            if self._in_json_ld:
                self.json_ld_parts.append("".join(self._json_ld_buffer))
                self._json_ld_buffer = []
                self._in_json_ld = False
            elif self._ignore_depth:
                self._ignore_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_heading = False
        elif tag == "a":
            if self._current_link:
                self.links.append(
                    PageLink(
                        url=self._current_link,
                        text=" ".join(self._link_text).strip(),
                    )
                )
            self._current_link = None
            self._link_text = []
        elif tag == "button" and self._button_text is not None:
            self.application_controls.append(" ".join(self._button_text).strip())
            self._button_text = None

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return

        if self._in_json_ld:
            self._json_ld_buffer.append(data)
            return

        if self._ignore_depth:
            return

        if self._capture_apply_badge:
            self._apply_area_badge.append(text)
        if self._capture_apply_button:
            self._apply_area_button.append(text)

        if self._in_title:
            self.title_parts.append(text)
        if self._in_heading and self.heading_parts:
            self.heading_parts[-1] += f" {text}"
        if self._current_link is not None:
            self._link_text.append(text)
        if self._button_text is not None:
            self._button_text.append(text)

        self.text_parts.append(text)

    def has_job_posting_schema(self) -> bool:
        for raw_json in self.json_ld_parts:
            try:
                if _contains_job_posting(json.loads(raw_json)):
                    return True
            except json.JSONDecodeError:
                continue
        return False


def _check_public_host(url: str) -> None:
    parts = urlsplit(url)
    host = parts.hostname

    if parts.scheme != "https" or not host:
        raise ValueError("HTTPS URL만 접근할 수 있습니다.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("사용자 정보가 포함된 URL은 접근할 수 없습니다.")

    # DNS가 내부·루프백 주소를 가리키면 접근하지 않습니다.
    port = parts.port or 443
    if port != 443:
        raise ValueError("표준 HTTPS 포트만 접근할 수 있습니다.")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("도메인의 IP 주소를 확인할 수 없습니다.")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError(f"공개 IP가 아닌 주소로 연결됩니다: {host}")


def make_page_fetcher():
    """공개 HTTPS 페이지를 제한된 크기로 가져오는 함수를 만듭니다."""

    def fetch_page(url: str) -> FetchedPage:
        with httpx.Client(
            timeout=15.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            current_url = url
            for redirect_count in range(MAX_REDIRECTS + 1):
                _check_public_host(current_url)
                with client.stream(
                    "GET",
                    current_url,
                    headers={"User-Agent": "JobTracker/0.1"},
                ) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("목적지가 없는 리다이렉트입니다.")
                        if redirect_count >= MAX_REDIRECTS:
                            raise ValueError("리다이렉트 횟수 상한을 넘었습니다.")
                        current_url = urljoin(current_url, location)
                        continue

                    response.raise_for_status()

                    content_type = response.headers.get(
                        "content-type", ""
                    ).lower()
                    if "text/html" not in content_type:
                        raise ValueError("HTML 페이지가 아닙니다.")

                    chunks: list[bytes] = []
                    total_bytes = 0

                    for chunk in response.iter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > MAX_HTML_BYTES:
                            raise ValueError("HTML 응답 크기 상한을 넘었습니다.")
                        chunks.append(chunk)

                    html = b"".join(chunks).decode(
                        response.encoding or "utf-8",
                        errors="replace",
                    )
                    final_url = str(response.url)
                    status_code = response.status_code
                    break

        parser = JobPageParser()
        parser.feed(html)

        title = " ".join(parser.title_parts).strip()
        content = " ".join(parser.text_parts).strip()
        structured_deadline = _structured_deadline(parser.json_ld_parts)
        if structured_deadline:
            content = f"{structured_deadline} {content}"

        apply_words = ("지원하기", "즉시 지원", "홈페이지 지원", "입사지원", "apply now", "apply for")
        has_application_action = any(
            any(word in label.casefold() for word in apply_words)
            for label in [
                *(link.text for link in parser.links),
                *parser.application_controls,
            ]
        )
        saramin_status = _saramin_open_status(final_url, title)

        return FetchedPage(
            url=final_url,
            title=title,
            content=content[:MAX_TEXT_CHARS],
            links=parser.links,
            http_status=status_code,
            has_job_posting_schema=parser.has_job_posting_schema(),
            has_single_posting_heading=(
                len(parser.heading_parts) == 1
                and bool(parser.heading_parts[0].strip())
            ),
            has_application_action=has_application_action,
            active_application_evidence=(
                parser.active_application_evidence
                if urlsplit(final_url).hostname == "job.incruit.com"
                else saramin_status
            ),
            iframe_sources=parser.iframe_sources,
            embedded_detail_url=_jobkorea_embedded_detail_url(final_url, html),
        )

    return fetch_page
