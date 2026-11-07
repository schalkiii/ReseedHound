"""调度器：run_on_start / startup_delay / 退出信号 / 单轮状态传递。"""
from types import SimpleNamespace

import pytest

import src.scheduler as scheduler_module
from src.scheduler import ReseedScheduler
from src.storage.config import Config
from tests.helpers import write_yaml


def build_scheduler(config_files, monkeypatch, *, run_on_start=True,
                    startup_delay=0, wait_results=(True,), run_result=0,
                    cookiecloud_enabled=False):
    cfg_path, sites_path, cfg, _ = config_files
    cfg["scheduler"].update({
        "run_on_start": run_on_start,
        "startup_delay": startup_delay,
        "interval_minutes": 1,  # 60 秒，但等待被 fake 掉，不会真的睡
    })
    cfg["cookiecloud"] = {"enabled": cookiecloud_enabled}
    write_yaml(cfg_path, cfg)
    config = Config(str(cfg_path), str(sites_path))

    events = []
    wait_results = list(wait_results)

    async def fake_run_once(_config, _args):
        events.append(("run",))
        return run_result

    async def fake_wait(self, seconds):
        events.append(("wait", seconds))
        idx = sum(1 for e in events if e[0] == "wait") - 1
        return wait_results[idx] if idx < len(wait_results) else True

    async def fake_sync_cookies(self):
        events.append(("sync_cookies",))

    monkeypatch.setattr(scheduler_module, "run_once", fake_run_once)
    monkeypatch.setattr(ReseedScheduler, "_wait_with_stop", fake_wait)
    monkeypatch.setattr(ReseedScheduler, "_sync_cookies", fake_sync_cookies)
    monkeypatch.setattr(
        ReseedScheduler, "_install_signal_handlers", lambda self, loop: None
    )
    args = SimpleNamespace(dry_run=True, site=None, no_feishu=True)
    return ReseedScheduler(config, args), events


@pytest.mark.asyncio
async def test_run_on_start_false_waits_one_full_interval(config_files, monkeypatch):
    sched, events = build_scheduler(
        config_files, monkeypatch, run_on_start=False, wait_results=(True,)
    )
    assert await sched.run() == 0
    assert events == [("wait", 60.0)]
    assert sched._cycle == 0


@pytest.mark.asyncio
async def test_run_on_start_true_runs_immediately(config_files, monkeypatch):
    sched, events = build_scheduler(
        config_files, monkeypatch, run_on_start=True, wait_results=(True,)
    )
    assert await sched.run() == 0
    assert events == [("run",), ("wait", 60.0)]
    assert sched._cycle == 1


@pytest.mark.asyncio
async def test_startup_delay_precedes_first_run(config_files, monkeypatch):
    sched, events = build_scheduler(
        config_files, monkeypatch, startup_delay=5, wait_results=(False, True)
    )
    assert await sched.run() == 0
    assert events == [("wait", 5.0), ("run",), ("wait", 60.0)]
    assert sched._cycle == 1


@pytest.mark.asyncio
async def test_stop_during_startup_delay_runs_zero_cycles(config_files, monkeypatch):
    sched, events = build_scheduler(
        config_files, monkeypatch, startup_delay=5, wait_results=(True,)
    )
    assert await sched.run() == 0
    assert events == [("wait", 5.0)]
    assert sched._cycle == 0


@pytest.mark.asyncio
async def test_startup_delay_and_run_on_start_stack(config_files, monkeypatch):
    sched, events = build_scheduler(
        config_files, monkeypatch, run_on_start=False, startup_delay=5,
        wait_results=(False, True),
    )
    assert await sched.run() == 0
    assert events == [("wait", 5.0), ("wait", 60.0)]
    assert sched._cycle == 0


@pytest.mark.asyncio
async def test_nonzero_cycle_result_does_not_stop_scheduler(config_files, monkeypatch):
    sched, events = build_scheduler(
        config_files, monkeypatch, wait_results=(False, True), run_result=1
    )
    assert await sched.run() == 0
    assert events == [("run",), ("wait", 60.0), ("run",), ("wait", 60.0)]
    assert sched._cycle == 2


@pytest.mark.asyncio
async def test_cookiecloud_sync_runs_before_cycle(config_files, monkeypatch):
    sched, events = build_scheduler(
        config_files, monkeypatch, cookiecloud_enabled=True, wait_results=(True,)
    )
    assert await sched.run() == 0
    assert events == [("sync_cookies",), ("run",), ("wait", 60.0)]
