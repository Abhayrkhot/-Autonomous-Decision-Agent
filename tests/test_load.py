import asyncio
import json
import subprocess

import httpx
import pytest

from scripts import load


def samples(*latencies, status="200"):
    return [
        {
            "index": i,
            "start_offset_seconds": i / 10,
            "latency_seconds": value,
            "status": status,
        }
        for i, value in enumerate(latencies)
    ]


def test_summary_uses_consistent_inclusive_percentiles_and_success_only_latencies():
    report = load.summarize(samples(1, 2, 3, 4) + samples(999, status="500"), 2)
    assert report == {
        "attempts": 5,
        "successful_requests": 4,
        "statuses": {"200": 4, "500": 1},
        "elapsed_seconds": 2,
        "requests_per_second": 2.5,
        "successful_requests_per_second": 2,
        "p50_seconds": 2.5,
        "p95_seconds": pytest.approx(3.85),
        "p99_seconds": pytest.approx(3.97),
        "max_seconds": 4,
    }


@pytest.mark.parametrize("observations", [[], samples(3, status="error:ReadTimeout")])
def test_zero_successes_have_null_latency_percentiles(observations):
    report = load.summarize(observations, 1)
    assert report["successful_requests_per_second"] == 0
    assert all(
        report[key] is None
        for key in ("p50_seconds", "p95_seconds", "p99_seconds", "max_seconds")
    )


def test_single_success_is_its_own_percentile():
    report = load.summarize(samples(0.125), 1)
    assert all(
        report[key] == 0.125
        for key in ("p50_seconds", "p95_seconds", "p99_seconds", "max_seconds")
    )


@pytest.mark.parametrize("elapsed", [0, -1, float("nan"), float("inf")])
def test_invalid_elapsed_time_is_rejected(elapsed):
    with pytest.raises(ValueError, match="Elapsed seconds"):
        load.summarize([], elapsed)


@pytest.mark.parametrize(
    "status,dirty", [("", False), ("?? new-file\n", True), (" M app/main.py\n", True)]
)
def test_revision_reports_untracked_and_tracked_changes(monkeypatch, status, dirty):
    commands = []

    def git(args, **kwargs):
        commands.append(args)
        return "abc123\n" if args[1] == "describe" else status

    monkeypatch.setattr(load.subprocess, "check_output", git)
    assert load.client_revision() == {"revision": "abc123", "worktree_dirty": dirty}
    assert "--dirty" in commands[0]
    assert "--untracked-files=normal" in commands[1]


@pytest.mark.parametrize(
    "error", [FileNotFoundError(), subprocess.CalledProcessError(128, "git")]
)
def test_revision_is_optional_without_git_or_in_archive(monkeypatch, error):
    def missing(*args, **kwargs):
        raise error

    monkeypatch.setattr(load.subprocess, "check_output", missing)
    assert load.client_revision() == {"revision": None, "worktree_dirty": None}


@pytest.mark.anyio
async def test_batch_keeps_index_status_and_timing_for_every_attempt():
    active = peak = calls = 0

    async def handler(request):
        nonlocal active, peak, calls
        index = calls
        calls += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        if index == 1:
            raise httpx.ReadTimeout("simulated timeout")
        return httpx.Response(500 if index == 2 else 200)

    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    ) as client:
        report = await load.sample_batch(client, {}, count=5, concurrency=2)
    assert peak == 2
    assert [sample["index"] for sample in report["samples"]] == list(range(5))
    assert report["summary"]["statuses"] == {"200": 3, "500": 1, "error:ReadTimeout": 1}
    for sample in report["samples"]:
        assert sample["start_offset_seconds"] >= 0
        assert sample["latency_seconds"] > 0


@pytest.mark.anyio
async def test_measure_preflights_warms_each_run_and_configures_client(monkeypatch):
    paths = []
    options = {}
    original_client = httpx.AsyncClient

    def capture_client(**kwargs):
        options.update(kwargs)
        return original_client(**kwargs)

    async def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/agent/run":
            assert json.loads(request.content) == load.DEFAULT_PAYLOAD
        return httpx.Response(200)

    monkeypatch.setattr(load.httpx, "AsyncClient", capture_client)
    report = await load.measure(
        "http://test",
        3,
        2,
        timeout=7,
        warmup=2,
        repeat=2,
        transport=httpx.MockTransport(handler),
        server_notes="one worker",
        server_revision="server-123",
    )
    assert paths == ["/health"] + ["/agent/run"] * 10
    assert options["timeout"] == 7
    assert (
        options["limits"].max_connections
        == options["limits"].max_keepalive_connections
        == 2
    )
    assert report["all_succeeded"] is True
    assert len(report["runs"]) == 2
    for run in report["runs"]:
        assert run["warmup_summary"]["attempts"] == 2
        assert run["summary"]["attempts"] == len(run["samples"]) == 3
    assert report["server"]["revision_reported_by_operator"] == "server-123"
    assert report["server"]["notes_reported_by_operator"] == "one worker"
    assert report["workload"]["document_count"] == 3
    assert len(report["workload"]["payload_sha256"]) == 64
    assert report["started_at_utc"].endswith("+00:00")
    assert "cpu_count" in report["client"]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["timeout", "status"])
async def test_health_failure_returns_report_without_measured_requests(failure):
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        if failure == "timeout":
            raise httpx.ConnectTimeout("unavailable")
        return httpx.Response(503)

    report = await load.measure(
        "http://test", 1, 1, transport=httpx.MockTransport(handler)
    )
    assert paths == ["/health"]
    assert report["runs"] == []
    assert report["all_succeeded"] is False
    assert report["preflight_error"] == (
        "error:ConnectTimeout" if failure == "timeout" else "error:HTTPStatusError"
    )


@pytest.mark.anyio
async def test_failed_warmup_is_visible_even_when_measured_run_succeeds():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(500 if calls == 2 else 200)

    report = await load.measure(
        "http://test", 1, 1, warmup=1, transport=httpx.MockTransport(handler)
    )
    assert report["all_succeeded"] is False
    assert report["runs"][0]["warmup_summary"]["statuses"] == {"500": 1}
    assert report["runs"][0]["summary"]["statuses"] == {"200": 1}


@pytest.mark.anyio
@pytest.mark.parametrize("documents,document_count", [([], 0), (None, None)])
async def test_custom_payload_and_zero_warmup_record_failed_measurements(
    documents, document_count
):
    payload = {"objective": "custom", "documents": documents}

    async def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200)
        assert json.loads(request.content) == payload
        raise httpx.ReadError("connection lost")

    report = await load.measure(
        "http://test",
        2,
        10,
        warmup=0,
        payload=payload,
        transport=httpx.MockTransport(handler),
    )
    assert report["all_succeeded"] is False
    assert report["runs"][0]["warmup_summary"]["attempts"] == 0
    assert report["runs"][0]["summary"]["statuses"] == {"error:ReadError": 2}
    assert report["workload"]["document_count"] == document_count


@pytest.mark.anyio
@pytest.mark.parametrize(
    "options",
    [
        {"count": 0},
        {"count": True},
        {"concurrency": 0},
        {"concurrency": 1.5},
        {"warmup": -1},
        {"repeat": 0},
        {"timeout": 0},
        {"timeout": float("nan")},
    ],
)
async def test_measure_rejects_invalid_configuration_before_network(options):
    settings = {"count": 1, "concurrency": 1, **options}
    with pytest.raises(ValueError):
        await load.measure("http://unused", **settings)


@pytest.mark.parametrize("successful", [True, False])
@pytest.mark.parametrize("custom_payload", [True, False])
def test_cli_writes_report_and_returns_failure_exit_code(
    tmp_path, monkeypatch, successful, custom_payload
):
    captured = {}

    async def fake_measure(*args, **kwargs):
        captured.update(kwargs)
        return {"all_succeeded": successful, "observations": ["retained"]}

    monkeypatch.setattr(load, "measure", fake_measure)
    output = tmp_path / "report.json"
    args = ["--output", str(output)]
    if custom_payload:
        payload_file = tmp_path / "payload.json"
        payload_file.write_text('{"objective":"custom"}')
        args += ["--payload", str(payload_file)]
    assert load.main(args) == (0 if successful else 1)
    assert json.loads(output.read_text()) == {
        "all_succeeded": successful,
        "observations": ["retained"],
    }
    assert captured["payload"] == ({"objective": "custom"} if custom_payload else None)


@pytest.mark.parametrize("content", [None, "{broken", "[]"])
def test_cli_rejects_missing_malformed_or_nonobject_payload(tmp_path, content):
    payload = tmp_path / "payload.json"
    if content is not None:
        payload.write_text(content)
    with pytest.raises(SystemExit) as error:
        load.main(
            ["--payload", str(payload), "--output", str(tmp_path / "report.json")]
        )
    assert error.value.code == 2
    assert not (tmp_path / "report.json").exists()


def test_cli_rejects_invalid_measurement_configuration(tmp_path):
    with pytest.raises(SystemExit) as error:
        load.main(["--count", "0", "--output", str(tmp_path / "report.json")])
    assert error.value.code == 2
