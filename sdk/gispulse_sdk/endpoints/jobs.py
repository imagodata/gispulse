"""Job endpoints."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from gispulse_sdk.models import JobCreate, JobResponse

if TYPE_CHECKING:
    from gispulse_sdk.client import GISPulseClient


class JobsEndpoint:
    def __init__(self, client: GISPulseClient):
        self._c = client

    def create(self, job: JobCreate) -> JobResponse:
        resp = self._c._request("POST", "/jobs", json=job.model_dump(mode="json"))
        return JobResponse.model_validate(resp)

    def list(self, limit: int = 100, offset: int = 0) -> list[JobResponse]:
        resp = self._c._request("GET", "/jobs", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [JobResponse.model_validate(j) for j in items]

    def get(self, job_id: UUID | str) -> JobResponse:
        resp = self._c._request("GET", f"/jobs/{job_id}")
        return JobResponse.model_validate(resp)

    def cancel(self, job_id: UUID | str) -> dict:
        return self._c._request("POST", f"/jobs/{job_id}/cancel")

    def download(self, job_id: UUID | str, output_path: str | Path | None = None) -> Path:
        """Download job results to a local file."""
        resp = self._c._http.get(f"{self._c._base_url}/jobs/{job_id}/download")
        from gispulse_sdk.exceptions import raise_for_status

        raise_for_status(resp.status_code, resp.text if resp.status_code >= 300 else "")
        out = Path(output_path) if output_path else Path(f"job_{job_id}_result")
        out.write_bytes(resp.content)
        return out

    def run_and_wait(
        self,
        job: JobCreate,
        poll_interval: float = 2.0,
        timeout: float = 300.0,
    ) -> JobResponse:
        """Create a job, poll until it completes, and return the final state."""
        created = self.create(job)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.get(created.id)
            if current.status in ("completed", "failed"):
                return current
            time.sleep(poll_interval)
        return self.get(created.id)
