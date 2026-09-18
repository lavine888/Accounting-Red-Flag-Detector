"""PandaData provider: the single point of network access.

Design goals borrowed from the validated Buffett screener:

* credentials come from the environment and are removed from the process env
  after loading; they are never logged or written to disk;
* requests are throttled, optionally concurrent and retried on rate limits;
* every response is cached atomically with a manifest and a content hash, and
  cache namespaces bind the SDK version, base URL, account and field contract;
* ``source_provenance`` returns the aggregate hash of every response a run
  actually used, which becomes the run's ``source_snapshot``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any, Iterable

import pandas as pd

from ..config import REPORT_FIELDS
from ..point_in_time.universe import filter_a_share_universe, select_industries
from ..util import clean_date, clean_symbol


class ProviderError(RuntimeError):
    pass


class AuthenticationError(ProviderError):
    pass


def _batches(values: list[str], size: int) -> Iterable[list[str]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


class PandaDataProvider:
    name = "PandaData"
    requires_live_validation = False

    def __init__(self, *, username: str | None = None, password: str | None = None, base_url: str | None = None):
        self._credentials: tuple[str, str, str | None] | None = None
        if username or password:
            self.configure_credentials(username or "", password or "", base_url)
        self._cache_dir: Path | None = None
        self._min_request_interval = 0.0
        self._max_workers = 1
        self._last_request_at = 0.0
        self._request_lock = threading.Lock()
        self._auth_lock = threading.Lock()
        self._authenticated = False
        self._source_lock = threading.Lock()
        self._used_source_entries: set[str] = set()

    # --- configuration -----------------------------------------------------

    def configure_credentials(self, username: str, password: str, base_url: str | None = None) -> None:
        if not username or not password:
            raise AuthenticationError("PandaData username and password are required")
        self._credentials = (str(username), str(password), base_url)
        self._authenticated = False

    def configure_runtime(
        self, *, cache_dir: str | Path | None = None, min_request_interval: float = 0.0, max_workers: int = 1
    ) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._min_request_interval = max(0.0, float(min_request_interval))
        self._max_workers = max(1, int(max_workers))
        with self._source_lock:
            self._used_source_entries = set()

    def consume_environment_credentials(self) -> None:
        username = os.environ.pop("PANDA_DATA_USERNAME", None)
        password = os.environ.pop("PANDA_DATA_PASSWORD", None)
        base_url = os.environ.pop("PANDA_DATA_BASE_URL", None)
        self.configure_credentials(username or "", password or "", base_url)

    # --- versions / provenance --------------------------------------------

    @staticmethod
    def sdk_version() -> str:
        try:
            return importlib.metadata.version("panda-data")
        except importlib.metadata.PackageNotFoundError as exc:
            raise ProviderError("panda_data is not installed") from exc

    def runtime_versions(self) -> dict[str, str]:
        import numpy as np
        import pyarrow

        return {
            "panda_data": self.sdk_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "pyarrow": pyarrow.__version__,
        }

    def source_provenance(self) -> dict[str, Any]:
        with self._source_lock:
            entries = sorted(self._used_source_entries)
        digest = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
        return {"response_count": len(entries), "response_manifest_hash": digest}

    # --- auth --------------------------------------------------------------

    def ensure_authenticated(self) -> None:
        if self._authenticated:
            return
        import panda_data

        with self._auth_lock:
            if self._authenticated:
                return
            if self._credentials is None:
                self.consume_environment_credentials()
            assert self._credentials is not None
            username, password, base_url = self._credentials
            kwargs: dict[str, Any] = {"username": username, "password": password}
            if base_url:
                kwargs["base_url"] = base_url
            try:
                panda_data.init_token(**kwargs)
                self._authenticated = True
            except Exception as exc:  # pragma: no cover - network dependent
                raise AuthenticationError(
                    f"PandaData authentication failed: {type(exc).__name__}"
                ) from exc

    # --- cache -------------------------------------------------------------

    def _cache_context(self) -> dict[str, str]:
        username = self._credentials[0] if self._credentials else os.getenv("PANDA_DATA_USERNAME", "")
        base_url = (self._credentials[2] if self._credentials else os.getenv("PANDA_DATA_BASE_URL")) or "default"
        contract = json.dumps(REPORT_FIELDS, ensure_ascii=False, separators=(",", ":"))
        return {
            "sdk_version": self.sdk_version(),
            "base_url_hash": hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:16],
            "account_hash": hashlib.sha256(username.encode("utf-8")).hexdigest()[:16],
            "contract_hash": hashlib.sha256(contract.encode("utf-8")).hexdigest()[:16],
        }

    def _cache_path(self, name: str, kwargs: dict[str, Any]) -> Path | None:
        if self._cache_dir is None:
            return None
        context = self._cache_context()
        payload = json.dumps(
            {"name": name, "kwargs": kwargs, "context": context},
            ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        namespace = hashlib.sha256(json.dumps(context, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return self._cache_dir / namespace / name / f"{digest}.parquet"

    @staticmethod
    def _normalized_cache_frame(frame: pd.DataFrame) -> pd.DataFrame:
        cache_frame = frame.copy()
        for column in cache_frame.columns:
            dtype = cache_frame[column].dtype
            if dtype == object or isinstance(dtype, pd.StringDtype):
                cache_frame[column] = cache_frame[column].astype("string")
        return cache_frame

    @staticmethod
    def _frame_digest(frame: pd.DataFrame) -> str:
        normalized = PandaDataProvider._normalized_cache_frame(frame)
        metadata = json.dumps(
            {"columns": list(normalized.columns), "dtypes": [str(dtype) for dtype in normalized.dtypes]},
            ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")
        values = pd.util.hash_pandas_object(normalized, index=True).values.tobytes()
        return hashlib.sha256(metadata + values).hexdigest()

    def _record_source(self, cache_path: Path | None, frame_digest: str) -> None:
        request_id = cache_path.stem if cache_path is not None else "uncached"
        with self._source_lock:
            self._used_source_entries.add(f"{request_id}:{frame_digest}")

    # --- transport ---------------------------------------------------------

    @staticmethod
    def _call_with_timeout(api: Any, kwargs: dict[str, Any], timeout: float) -> Any:
        result: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                result.put((True, api(**kwargs)))
            except BaseException as exc:  # noqa: BLE001 - re-raised on caller thread
                result.put((False, exc))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise ProviderError("PandaData request timed out")
        ok, value = result.get_nowait()
        if not ok:
            raise value
        return value

    def _throttled_call(self, api: Any, kwargs: dict[str, Any], timeout: float) -> Any:
        with self._request_lock:
            delay = self._min_request_interval - (time.monotonic() - self._last_request_at)
            if delay > 0:
                time.sleep(delay)
            self._last_request_at = time.monotonic()
        return self._call_with_timeout(api, kwargs, timeout)

    def fetch(self, name: str, *, timeout: float = 60, retries: int = 8, **kwargs: Any) -> pd.DataFrame:
        cache_path = self._cache_path(name, kwargs)
        if cache_path is not None and cache_path.exists():
            manifest_path = cache_path.with_suffix(".json")
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                frame = pd.read_parquet(cache_path)
                digest = self._frame_digest(frame)
                if manifest.get("frame_sha256") == digest:
                    self._record_source(cache_path, digest)
                    return frame
            except Exception:
                pass
        self.ensure_authenticated()
        import panda_data

        api = getattr(panda_data, name, None)
        if not callable(api):
            raise ProviderError(f"panda_data {self.sdk_version()} has no {name}")
        for attempt in range(retries):
            try:
                result = self._throttled_call(api, kwargs, timeout)
                if result is None:
                    frame = pd.DataFrame()
                else:
                    frame = result.copy() if isinstance(result, pd.DataFrame) else pd.DataFrame(result)
                if cache_path is not None:
                    self._write_cache(cache_path, frame)
                digest = self._frame_digest(frame)
                self._record_source(cache_path, digest)
                return frame
            except Exception as exc:
                message = str(exc)
                rate_limited = "500010" in message or "请求次数超限" in message
                if rate_limited and attempt + 1 < retries:
                    time.sleep(min(60, 15 * (attempt + 1)))
                    continue
                raise ProviderError(f"{name} failed: {type(exc).__name__}: {exc}") from exc
        return pd.DataFrame()

    def _write_cache(self, cache_path: Path, frame: pd.DataFrame) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(f".{os.getpid()}.tmp.parquet")
        try:
            cache_frame = self._normalized_cache_frame(frame)
            frame_sha256 = self._frame_digest(cache_frame)
            cache_frame.to_parquet(temporary, index=False)
            temporary.replace(cache_path)
            manifest = {
                "fetched_at": datetime.now().astimezone().isoformat(),
                "api": cache_path.parent.name,
                "row_count": len(cache_frame),
                "columns": list(cache_frame.columns),
                "dtypes": [str(dtype) for dtype in cache_frame.dtypes],
                "frame_sha256": frame_sha256,
                "context": self._cache_context(),
            }
            manifest_path = cache_path.with_suffix(".json")
            manifest_tmp = manifest_path.with_suffix(f".{os.getpid()}.tmp.json")
            manifest_tmp.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
            )
            manifest_tmp.replace(manifest_path)
        except Exception:
            # Cache availability must never turn a successful provider call into
            # a failed screen.
            pass
        finally:
            temporary.unlink(missing_ok=True)

    # --- high level loaders ------------------------------------------------

    def discover_universe(self, as_of: str) -> list[str]:
        frame = self.fetch("get_stock_detail", status=None)
        universe = filter_a_share_universe(frame, as_of)
        if not universe:
            raise ProviderError("get_stock_detail returned no A-share universe")
        return universe

    def fetch_reports(self, symbols: list[str], as_of: str, years: int = 8) -> pd.DataFrame:
        latest_year = int(clean_date(as_of)[:4])
        first_year = latest_year - years
        jobs: list[tuple[list[str], int, int]] = []
        for batch in _batches(symbols, 20):
            chunk_start = first_year
            while chunk_start <= latest_year:
                chunk_end = min(chunk_start + 4, latest_year)
                jobs.append((batch, chunk_start, chunk_end))
                chunk_start = chunk_end + 1

        def load(job: tuple[list[str], int, int]) -> pd.DataFrame:
            batch, chunk_start, chunk_end = job
            return self.fetch(
                "get_fina_reports",
                symbol=batch,
                start_quarter=f"{chunk_start}q1",
                end_quarter=f"{chunk_end}q4",
                date=clean_date(as_of),
                is_latest=False,
                fields=REPORT_FIELDS,
            )

        if self._max_workers == 1:
            loaded = list(map(load, jobs))
        else:
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                loaded = list(pool.map(load, jobs))
        frames = [frame for frame in loaded if not frame.empty]
        return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame(columns=REPORT_FIELDS)

    def fetch_industries(self, symbols: list[str], as_of: str) -> dict[str, dict[str, str]]:
        frames: list[pd.DataFrame] = []
        for batch in _batches(symbols, 100):
            frame = self.fetch(
                "get_industry_constituents",
                stock_symbol=batch,
                level="L1",
                fields=["stock_symbol", "l1_code", "in_date", "out_date"],
            )
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return {}
        constituents = pd.concat(frames, ignore_index=True, sort=False)
        details = self.fetch("get_industry_detail", level="L1", fields=["industry_code", "industry_name"])
        return select_industries(constituents, details, as_of)

    def fetch_latest_prices(self, symbols: list[str], as_of: str) -> dict[str, dict[str, Any]]:
        end = datetime.strptime(clean_date(as_of), "%Y%m%d")
        start = (end - timedelta(days=30)).strftime("%Y%m%d")
        frames: list[pd.DataFrame] = []
        for batch in _batches(symbols, 50):
            frame = self.fetch(
                "get_stock_daily",
                symbol=batch,
                start_date=start,
                end_date=clean_date(as_of),
                fields=["symbol", "date", "close"],
            )
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return {}
        work = pd.concat(frames, ignore_index=True, sort=False)
        work["symbol"] = work["symbol"].map(clean_symbol)
        work["date"] = work["date"].map(clean_date)
        work["close"] = pd.to_numeric(work["close"], errors="coerce")
        work = work[work["date"].ne("") & (work["date"] <= clean_date(as_of)) & work["close"].gt(0)]
        work = work.sort_values(["symbol", "date"]).drop_duplicates("symbol", keep="last")
        return {
            str(row["symbol"]): {"date": str(row["date"]), "close": float(row["close"])}
            for _, row in work.iterrows()
        }

    def fetch_forward_returns(self, symbols: list[str], start_date: str, end_date: str) -> dict[str, float]:
        """Post-adjusted return between the first close after start and the last close by end."""

        start = (datetime.strptime(clean_date(start_date), "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        frames: list[pd.DataFrame] = []
        for batch in _batches(symbols, 50):
            frame = self.fetch(
                "get_stock_daily_post",
                symbol=batch,
                start_date=start,
                end_date=clean_date(end_date),
                fields=["symbol", "date", "close"],
            )
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return {}
        work = pd.concat(frames, ignore_index=True, sort=False)
        work["symbol"] = work["symbol"].map(clean_symbol)
        work["date"] = work["date"].map(clean_date)
        work["close"] = pd.to_numeric(work["close"], errors="coerce")
        work = work[
            work["symbol"].notna()
            & work["date"].ne("")
            & work["close"].notna()
            & work["date"].between(start, clean_date(end_date))
        ].sort_values(["symbol", "date"])
        returns: dict[str, float] = {}
        for symbol, group in work.groupby("symbol"):
            if len(group) < 2 or float(group.iloc[0]["close"]) <= 0:
                continue
            returns[str(symbol)] = float(group.iloc[-1]["close"] / group.iloc[0]["close"] - 1)
        return returns
