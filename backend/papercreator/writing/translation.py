"""Academic translation helpers with explicit privacy/cost boundaries."""

from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..core.errors import ProviderError, ValidationError

ACADEMIC_GLOSSARY: dict[str, str] = {
    "ablation study": "消融实验",
    "abstract": "摘要",
    "baseline": "基线方法",
    "bias": "偏倚",
    "causal inference": "因果推断",
    "confidence interval": "置信区间",
    "confounding variable": "混杂变量",
    "construct validity": "构念效度",
    "control group": "对照组",
    "dataset": "数据集",
    "effect size": "效应量",
    "empirical study": "实证研究",
    "external validity": "外部效度",
    "fine-tuning": "微调",
    "generalisation": "泛化",
    "ground truth": "参考真值",
    "hypothesis": "假设",
    "internal validity": "内部效度",
    "large language model": "大语言模型",
    "literature review": "文献综述",
    "methodology": "研究方法",
    "peer review": "同行评审",
    "precision": "精确率",
    "preprint": "预印本",
    "qualitative research": "定性研究",
    "quantitative research": "定量研究",
    "recall": "召回率",
    "reproducibility": "可复现性",
    "research gap": "研究空白",
    "research question": "研究问题",
    "robustness": "稳健性",
    "statistical significance": "统计显著性",
    "systematic review": "系统综述",
    "threats to validity": "有效性威胁",
    "training set": "训练集",
    "validation set": "验证集",
}


def glossary_lookup(text: str, source: str, target: str) -> dict[str, Any]:
    term = re.sub(r"\s+", " ", text).strip()
    if not term:
        raise ValidationError("translation text is empty")
    if source.startswith("en") and target.startswith("zh"):
        translated = ACADEMIC_GLOSSARY.get(term.casefold(), "")
    elif source.startswith("zh") and target.startswith("en"):
        reverse = {value: key for key, value in ACADEMIC_GLOSSARY.items()}
        translated = reverse.get(term, "")
    else:
        translated = ""
    return {
        "text": translated,
        "found": bool(translated),
        "provider": "builtin-glossary",
        "source": source,
        "target": target,
        "note": "Curated offline terminology; exact-term lookup only.",
    }


@dataclass(frozen=True)
class TranslationSegment:
    text: str
    translate: bool


_PROTECTED_BLOCK = re.compile(r"(```[\s\S]*?```|\$\$[\s\S]*?\$\$)")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?。！？])(?=\s)")


def _plain_segments(text: str, limit: int) -> list[TranslationSegment]:
    segments: list[TranslationSegment] = []
    position = 0
    while position < len(text):
        if text[position].isspace():
            end = position + 1
            while end < len(text) and text[end].isspace():
                end += 1
            segments.append(TranslationSegment(text[position:end], False))
            position = end
            continue
        remaining = text[position:]
        if len(remaining) <= limit:
            body = remaining.rstrip()
            if body:
                segments.append(TranslationSegment(body, True))
            if len(body) < len(remaining):
                segments.append(TranslationSegment(remaining[len(body):], False))
            break
        window = remaining[:limit + 1]
        sentence_cuts = [match.start() for match in _SENTENCE_BREAK.finditer(window)]
        useful = [cut for cut in sentence_cuts if cut >= limit // 3]
        if useful:
            cut = useful[-1]
        else:
            whitespace = [index for index, char in enumerate(window[:limit]) if char.isspace()]
            cut = whitespace[-1] if whitespace and whitespace[-1] >= limit // 3 else limit
        body = remaining[:cut].rstrip()
        if not body:
            body = remaining[:limit]
            cut = limit
        segments.append(TranslationSegment(body, True))
        # Any whitespace removed from the request remains an exact, local-only
        # separator in the reconstructed result.
        trimmed = len(remaining[:cut]) - len(body)
        if trimmed:
            segments.append(TranslationSegment(remaining[cut - trimmed:cut], False))
        position += cut
    return segments


def _segments(text: str, limit: int = 450) -> list[TranslationSegment]:
    """Stable chunks that preserve whitespace, fenced code and display math."""
    if limit < 50:
        raise ValueError("translation chunk limit must be at least 50 characters")
    segments: list[TranslationSegment] = []
    for part in _PROTECTED_BLOCK.split(text):
        if not part:
            continue
        if _PROTECTED_BLOCK.fullmatch(part):
            segments.append(TranslationSegment(part, False))
        else:
            segments.extend(_plain_segments(part, limit))
    return segments


def _chunks(text: str, limit: int = 450) -> list[str]:
    """Compatibility helper returning only text that leaves the machine."""
    return [segment.text for segment in _segments(text, limit) if segment.translate]


async def _cooperative_wait(
    seconds: float,
    *,
    checkpoint: Callable[[], None] | None,
    sleep: Callable[[float], Awaitable[None]],
) -> None:
    remaining = max(0.0, seconds)
    while remaining > 0:
        if checkpoint:
            checkpoint()
        step = min(0.25, remaining)
        await sleep(step)
        remaining -= step


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After", "").strip()
    if not raw:
        return None
    try:
        return max(0.0, min(60.0, float(raw)))
    except ValueError:
        return None


async def mymemory_translate(
    text: str,
    source: str,
    target: str,
    *,
    client: httpx.AsyncClient | None = None,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    max_characters: int = 100_000,
    max_requests: int = 250,
    max_retries: int = 3,
    min_interval_s: float = 0.2,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    request_state: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Translate with bounded traffic, Retry-After support and cancellation."""
    if not text.strip():
        raise ValidationError("translation text is empty")
    if len(text) > max_characters:
        raise ValidationError(
            f"MyMemory translation is limited to {max_characters:,} characters per job"
        )
    segments = _segments(text)
    request_count = sum(1 for segment in segments if segment.translate)
    if request_count > max_requests:
        raise ValidationError(
            f"MyMemory translation needs {request_count} requests; the per-job limit is {max_requests}"
        )
    translated: list[str] = []
    completed = 0
    retries = 0
    throttle = request_state if request_state is not None else {"last_request_at": 0.0}
    owned_client = client is None
    active_client = client or httpx.AsyncClient(timeout=30.0, follow_redirects=False)
    try:
        for segment in segments:
            if not segment.translate:
                translated.append(segment.text)
                continue
            value = ""
            for attempt in range(max_retries + 1):
                if checkpoint:
                    checkpoint()
                last_request_at = float(throttle.get("last_request_at") or 0.0)
                elapsed = time.monotonic() - last_request_at
                if last_request_at and elapsed < min_interval_s:
                    await _cooperative_wait(
                        min_interval_s - elapsed, checkpoint=checkpoint, sleep=sleep
                    )
                try:
                    throttle["last_request_at"] = time.monotonic()
                    response = await active_client.get(
                        "https://api.mymemory.translated.net/get",
                        params={"q": segment.text, "langpair": f"{source}|{target}"},
                        headers={"User-Agent": "PaperCreator/0.1 academic translation"},
                    )
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt >= max_retries:
                        raise ProviderError(
                            f"MyMemory request failed after {attempt + 1} attempts: {exc}",
                            details={
                                "outcome": "timeout" if isinstance(exc, httpx.TimeoutException) else "network_error",
                                "retryable": True,
                                "provider": "mymemory",
                            },
                        ) from exc
                    delay = min(8.0, 0.5 * (2 ** attempt))
                else:
                    if response.status_code == 200:
                        try:
                            payload = response.json()
                        except ValueError as exc:
                            raise ProviderError(
                                "MyMemory returned invalid JSON",
                                details={"outcome": "invalid_response", "retryable": False, "provider": "mymemory"},
                            ) from exc
                        response_status = int(payload.get("responseStatus") or 200)
                        if response_status != 200:
                            raise ProviderError(
                                f"MyMemory payload reported status {response_status}",
                                details={
                                    "outcome": "rate_limited" if response_status == 429 else "invalid_response",
                                    "http_status": response_status,
                                    "retryable": response_status == 429 or response_status >= 500,
                                    "provider": "mymemory",
                                },
                            )
                        value = html.unescape(
                            str(payload.get("responseData", {}).get("translatedText") or "")
                        ).strip()
                        if not value:
                            raise ProviderError(
                                "MyMemory returned no translated text",
                                details={"outcome": "invalid_response", "retryable": False, "provider": "mymemory"},
                            )
                        break
                    retryable = response.status_code == 429 or response.status_code >= 500
                    if not retryable or attempt >= max_retries:
                        raise ProviderError(
                            f"MyMemory returned HTTP {response.status_code}",
                            details={
                                "outcome": "rate_limited" if response.status_code == 429 else "http_error",
                                "http_status": response.status_code,
                                "retry_after_s": _retry_after(response),
                                "retryable": retryable,
                                "provider": "mymemory",
                            },
                        )
                    delay = _retry_after(response) or min(8.0, 0.5 * (2 ** attempt))
                retries += 1
                await _cooperative_wait(delay, checkpoint=checkpoint, sleep=sleep)
            translated.append(value)
            completed += 1
            if progress:
                progress(completed, request_count, f"translated chunk {completed}/{request_count}")
    finally:
        if owned_client:
            await active_client.aclose()
    return {
        "text": "".join(translated),
        "provider": "mymemory",
        "source": source,
        "target": target,
        "chunks": request_count,
        "requests": request_count + retries,
        "retries": retries,
        "note": "Public external service; text was sent to api.mymemory.translated.net.",
    }


async def llm_translate(text: str, source: str, target: str) -> dict[str, Any]:
    from ..llm.client import complete_text

    source_name = "Chinese" if source.startswith("zh") else "English"
    target_name = "Chinese" if target.startswith("zh") else "English"
    system = (
        "You are a professional academic translator. Preserve citations, author "
        "names, equations, Markdown, units and terminology consistency. Do not "
        "summarise, explain or add claims. Return only the translation."
    )
    prompt = (
        f"Translate the following {source_name} academic text into {target_name}.\n\n"
        f"<text>\n{text}\n</text>"
    )
    translated = await complete_text(
        prompt,
        system=system,
        role="chat",
        temperature=0.1,
        purpose="academic_translation",
    )
    return {
        "text": translated.strip(),
        "provider": "llm",
        "source": source,
        "target": target,
        "note": "Generated by the configured LLM provider; verify specialist terminology.",
    }
