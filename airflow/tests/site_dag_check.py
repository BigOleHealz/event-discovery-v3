"""Execute both real mapped site DAGs twice, with fixture HTTP services supplied by the test."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from airflow.dag_processing.dagbag import DagBag
from airflow.utils import db

from ingestion.site_policy import SourcePolicy

db.initdb()
with patch("ingestion.clock.utc_now", return_value=datetime(2026, 9, 27, 12, tzinfo=UTC)) as clock:
    bag = DagBag(dag_folder="/opt/airflow/dags/ingest_site_stagehand.py")


def advance(seconds: float) -> None:
    clock.return_value += timedelta(seconds=seconds)


assert not bag.import_errors, bag.import_errors
for name in ("ingest_site_stagehand", "ingest_site_generic"):
    dag = bag.dags[name]
    chain = ("configured_targets",) + tuple(
        "ingest_target." + name
        for name in (
            "open_run",
            "fetch_pages",
            "collect_ids",
            "fetch_details",
            "parse_listings",
            "close_run",
        )
    )
    assert set(dag.task_dict) == set(chain)
    assert not dag.catchup and dag.max_active_runs == 1
    for left, right in zip(chain, chain[1:], strict=False):
        assert left in dag.task_dict[right].upstream_task_ids
    assert dag.task_dict["ingest_target.fetch_pages"].retries == 2
    assert dag.task_dict["ingest_target.fetch_pages"].retry_delay == timedelta(minutes=1)
    for task in dag.tasks:
        task.retries = 0  # A deliberately blocked target must not delay this offline test.
    for hour in (0, 1):
        with patch(
            "ingestion.site_jobs.SourcePolicy",
            side_effect=lambda url, now: SourcePolicy(url, now, advance),
        ):
            run = dag.test(logical_date=datetime(2026, 9, 27, hour, tzinfo=UTC))
        assert run.state == ("failed" if name == "ingest_site_stagehand" else "success")
