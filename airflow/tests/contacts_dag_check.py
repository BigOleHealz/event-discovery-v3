"""Execute the contacts DAG twice in Airflow's real harness."""

from datetime import UTC, datetime, timedelta

from airflow.dag_processing.dagbag import DagBag
from airflow.utils import db

db.initdb()
bag = DagBag(dag_folder="/opt/airflow/dags/match_contacts_to_users.py")
assert not bag.import_errors, bag.import_errors
dag = bag.dags["match_contacts_to_users"]
assert set(dag.task_dict) == {"match_contacts"}
assert dag.schedule == "15 2 * * *"
assert not dag.catchup
assert dag.max_active_runs == 1
task = dag.task_dict["match_contacts"]
assert task.retries == 2
assert task.retry_delay == timedelta(minutes=1)
for day in (28, 29):
    run = dag.test(logical_date=datetime(2026, 9, day, 10, tzinfo=UTC))
    assert run.state == "success", run.state
