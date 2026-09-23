"""Run inside the disposable Airflow container used by test_dedup_dag."""

from datetime import UTC, datetime, timedelta

from airflow.dag_processing.dagbag import DagBag
from airflow.utils import db

db.initdb()
bag = DagBag(dag_folder="/opt/airflow/dags/dedup_pending.py")
assert not bag.import_errors, bag.import_errors
dag = bag.dags["dedup_pending"]
assert set(dag.task_dict) == {"embed_listings", "resolve_listings"}
assert dag.task_dict["resolve_listings"].upstream_task_ids == {"embed_listings"}
assert not dag.catchup
assert dag.max_active_runs == 1
for task in dag.tasks:
    assert task.retries == 2
    assert task.retry_delay == timedelta(minutes=1)

for day in (16, 17):
    run = dag.test(logical_date=datetime(2026, 9, day, tzinfo=UTC))
    assert run.state == "success", run.state
