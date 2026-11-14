"""US-010: the observability configs parse, and every metric they use is one the services define."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from prometheus_client import REGISTRY

import tela_api.core.metrics  # noqa: F401 - registers the API metrics
import tela_gateway.metrics  # noqa: F401
import tela_runner.metrics  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
OBS = ROOT / "deploy" / "observability"
METRIC = re.compile(r"\btela_[a-z_]+\b")


def _defined() -> set[str]:
    return set(REGISTRY._names_to_collectors)  # includes the _total/_bucket/_count series names


def _used(text: str) -> set[str]:
    return set(METRIC.findall(text))


def test_every_config_parses() -> None:
    for name in ("otel-collector.yaml", "prometheus.yml", "alerts.yml", "loki.yaml", "tempo.yaml"):
        assert yaml.safe_load((OBS / name).read_text()), name
    for path in (OBS / "grafana" / "provisioning").rglob("*.yaml"):
        assert yaml.safe_load(path.read_text()), path
    assert json.loads((OBS / "grafana" / "dashboards" / "tela-overview.json").read_text())["panels"]


def test_alert_rules_only_use_defined_metrics() -> None:
    rules = yaml.safe_load((OBS / "alerts.yml").read_text())
    exprs = [r["expr"] for g in rules["groups"] for r in g["rules"] if "expr" in r]
    assert exprs
    for expr in exprs:
        assert _used(expr) <= _defined(), (expr, _used(expr) - _defined())


def test_dashboard_only_uses_defined_metrics() -> None:
    board = json.loads((OBS / "grafana" / "dashboards" / "tela-overview.json").read_text())
    for panel in board["panels"]:
        for target in panel["targets"]:
            assert _used(target["expr"]) <= _defined(), (panel["title"], _used(target["expr"]) - _defined())


def test_prometheus_scrapes_every_service_that_exposes_metrics() -> None:
    cfg = yaml.safe_load((OBS / "prometheus.yml").read_text())
    jobs = {j["job_name"] for j in cfg["scrape_configs"]}
    assert jobs == {"tela-api", "tela-llm-gateway", "tela-connector-runner"}
    assert cfg["rule_files"] == ["/etc/prometheus/alerts.yml"]


def test_the_collector_removes_what_logs_must_never_hold() -> None:
    cfg = yaml.safe_load((OBS / "otel-collector.yaml").read_text())
    dropped = {a["key"] for a in cfg["processors"]["attributes/redact"]["actions"] if a["action"] == "delete"}
    assert {"authorization", "password", "prompt", "completion", "rows"} <= dropped
    assert "attributes/redact" in cfg["service"]["pipelines"]["logs"]["processors"]
