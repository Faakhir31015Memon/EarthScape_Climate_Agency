"""Abstract base for every provider connector.

Contract: a connector PLANS and PERFORMS real retrieval (never fabricates),
NORMALISES provider payloads into canonical observations
(``schemas/observation.schema.json``) and VALIDATES them through the shared
validation engine.  If retrieval is impossible (missing token, network down,
provider error) the result status is ``unavailable``/``error`` with an honest,
sanitised reason — never fake records.
"""

from __future__ import annotations

import abc
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from pipeline_config import ENV  # noqa: F401  (connectors read provider config through ENV)

STATUSES = ("ok", "partial", "unavailable", "error")


class ConnectorNotConfigured(Exception):
    """Raised when a required credential/configuration is missing.

    The message always names the exact environment variable so operators can
    fix the setup instead of guessing.
    """

    def __init__(self, env_var: str, hint: str = "") -> None:
        self.env_var = env_var
        super().__init__(f"{env_var} is not set. {hint}".strip())


class ConnectorUnavailable(Exception):
    """Raised when the provider could not be reached; carries the honest reason."""


def sanitize_error(text: str) -> str:
    """Strip URLs, credentials and query-string secrets from error text.

    ``token=abc123`` style parameters, ``user:pass@`` fragments and full URLs
    are reduced to non-revealing placeholders so messages can be logged and
    shown in the product without leaking secrets.
    """
    if text is None:
        return ""
    out = str(text)
    out = re.sub(r"(?i)\bhttps?://\S+", "<url-redacted>", out)
    out = re.sub(r"(?i)\b(?:token|api_key|apikey|key|password|secret|authorization)\b\s*[=:]\s*[^\s,&]+",
                 r"<param>=<redacted>", out)
    out = re.sub(r"(?i)[A-Za-z0-9._%+-]+:[A-Za-z0-9._%+-]+@", "<credentials>@", out)
    out = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._\-]+", "Bearer <redacted>", out)
    return out


@dataclass
class ConnectorResult:
    """Uniform outcome of one connector run."""

    records: list = field(default_factory=list)
    fetched: int = 0
    accepted: int = 0
    rejected: int = 0
    skipped: int = 0
    errors: list = field(default_factory=list)
    status: str = "ok"
    coverage: dict = field(default_factory=dict)
    provenance_extra: dict = field(default_factory=dict)

    def add_error(self, where: str, exc: BaseException) -> None:
        self.errors.append({"where": where, "error": type(exc).__name__,
                            "message": sanitize_error(str(exc))})

    def as_dict(self) -> dict:
        return {
            "records": len(self.records),
            "fetched": self.fetched,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "skipped": self.skipped,
            "errors": self.errors,
            "status": self.status,
            "coverage": self.coverage,
        }


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class SourceConnector(abc.ABC):
    """Abstract interface implemented by every provider."""

    #: registry key, e.g. "open_meteo"
    name: str = ""
    #: "api" | "file" | "stream" | "sample"
    kind: str = "api"
    #: observation provider enum from the schema
    provider: str = ""

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config: dict = dict(config or {})
        self.timeout: float = float(self.config.get("timeout", ENV.number("CONNECTOR_TIMEOUT_SECONDS", 30.0)))
        self.max_attempts: int = int(self.config.get("max_attempts", ENV.integer("CONNECTOR_MAX_ATTEMPTS", 4)))
        self.backoff_base: float = float(self.config.get("backoff_base", 1.5))
        self.backoff_cap: float = float(self.config.get("backoff_cap", 30.0))

    # ------------------------------------------------------------------ API
    @abc.abstractmethod
    def plan(self) -> list:
        """Return the concrete request/unit plans this connector will execute.

        Each item is a dict describing one retrieval call (endpoint, params,
        logical segment).  Planning never touches the network so callers can
        show what WILL be fetched.
        """

    @abc.abstractmethod
    def fetch(self, result: "ConnectorResult") -> Iterable[dict]:
        """Perform real retrieval; yield raw provider payloads (dicts).

        Must raise :class:`ConnectorUnavailable` with an honest reason when the
        provider cannot be reached, and :class:`ConnectorNotConfigured` when a
        required env var is missing.  Never synthesises data.
        """

    @abc.abstractmethod
    def normalize(self, raw_payload: dict) -> list:
        """Convert one raw payload into canonical observation dicts (no quality block)."""

    def provenance(self) -> dict:
        """Base provenance attached to every record produced by this run."""
        return {
            "ingested_at": self._run_started_at,
            "schema_version": "1.0.0",
            "unit_source": self.provider,
            "transformations": list(self.transformations),
        }

    def run(self) -> "ConnectorResult":
        """Full pipeline: fetch -> normalize -> validate -> provenance.

        Counts are honest: ``fetched`` counts raw payload units, ``accepted``
        counts validated records, ``rejected`` counts records the validation
        engine rejected, ``skipped`` counts missing/None measurements that were
        omitted rather than fabricated.
        """
        self._run_started_at = now_utc()
        self.transformations: list = []
        result = ConnectorResult()
        try:
            payloads = list(self.fetch(result))
        except ConnectorNotConfigured as exc:
            result.status = "unavailable"
            result.add_error("fetch", exc)
            return result
        except ConnectorUnavailable as exc:
            result.status = "unavailable"
            result.add_error("fetch", exc)
            return result
        except Exception as exc:  # network stack raised something unhandled
            result.status = "error"
            result.add_error("fetch", exc)
            return result

        result.fetched = len(payloads)
        validator = self.validate()
        for payload in payloads:
            try:
                records = self.normalize(payload)
            except Exception as exc:
                result.add_error("normalize", exc)
                continue
            for rec in records:
                if rec is None:
                    result.skipped += 1
                    continue
                ts_source = rec.pop("_timestamp_source", None)
                validated, issues = validator(rec)
                if validated is None:
                    result.rejected += 1
                    result.errors.extend({"where": "validate", "error": i["rule"], "message": i["detail"]}
                                         for i in issues)
                    continue
                validated["provenance"] = self.provenance()
                if ts_source:
                    validated["provenance"]["timestamp_source"] = ts_source
                result.records.append(validated)
                result.accepted += 1

        if result.rejected and not result.records:
            result.status = "error"
        elif result.errors or result.rejected:
            result.status = "partial"
        else:
            result.status = "ok"
        return result

    # ------------------------------------------------------------- shared
    def validate(self):
        """Return the record validator: takes a dict, yields (record, issues)."""
        from validation.quality import validate_record

        def _validator(rec: dict):
            outcome = validate_record(rec)
            return outcome.record, outcome.issues

        return _validator

    def http_get_json(self, url: str, params: Optional[dict] = None,
                      headers: Optional[dict] = None) -> dict:
        """GET with retry/backoff+jitter, 429 Retry-After handling and timeouts."""
        import requests

        attempt = 0
        while True:
            attempt += 1
            try:
                resp = requests.get(url, params=params, headers=headers or {}, timeout=self.timeout)
            except requests.exceptions.RequestException as exc:
                if attempt >= self.max_attempts:
                    raise ConnectorUnavailable(sanitize_error(
                        f"provider unreachable after {attempt} attempts: {type(exc).__name__}")) from exc
                time.sleep(self._sleep(attempt))
                continue
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else self._backoff(attempt)
                if attempt >= self.max_attempts:
                    raise ConnectorUnavailable(
                        f"provider rate-limited (HTTP 429) after {attempt} attempts; not retrying further")
                time.sleep(min(wait, self.backoff_cap))
                continue
            if resp.status_code == 401 or resp.status_code == 403:
                raise ConnectorUnavailable(
                    f"provider rejected credentials (HTTP {resp.status_code}); check the configured token")
            if resp.status_code >= 400:
                if attempt < self.max_attempts and resp.status_code >= 500:
                    time.sleep(self._sleep(attempt))
                    continue
                raise ConnectorUnavailable(f"provider returned HTTP {resp.status_code}")
            self._capture_rate_limit_headers(resp.headers)
            try:
                return resp.json()
            except ValueError as exc:
                raise ConnectorUnavailable("provider returned a non-JSON body") from exc

    def _capture_rate_limit_headers(self, headers) -> None:
        captured = {k.lower(): v for k, v in headers.items()
                    if k.lower().startswith("x-ratelimit") or k.lower() in ("retry-after",)}
        if captured:
            if not hasattr(self, "rate_limit_seen"):
                self.rate_limit_seen = []
            self.rate_limit_seen.append(captured)

    def _backoff(self, attempt: int) -> float:
        base = min(self.backoff_cap, self.backoff_base * (2 ** (attempt - 1)))
        return base

    def _sleep(self, attempt: int) -> float:
        delay = self._backoff(attempt)
        jitter = random.uniform(0, delay * 0.25)
        time.sleep(delay + jitter)
        return delay + jitter

    def _note_transform(self, text: str) -> str:
        if text not in self.transformations:
            self.transformations.append(text)
        return text

    def _base_record_skeleton(self) -> dict:
        return {
            "source": {
                "provider": self.provider or self.name,
                "kind": self.kind,
                "retrieved_at": self._run_started_at,
            },
        }
