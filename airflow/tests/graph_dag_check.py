"""Execute the real DAG, delete the graph, then rerun twice in Airflow's harness."""

import os
from datetime import UTC, datetime, timedelta

from airflow.dag_processing.dagbag import DagBag
from airflow.utils import db
from neo4j import GraphDatabase

db.initdb()
bag = DagBag(dag_folder="/opt/airflow/dags/project_to_neo4j.py")
assert not bag.import_errors, bag.import_errors
dag = bag.dags["project_to_neo4j"]
assert set(dag.task_dict) == {"rebuild_projection"}
assert dag.schedule == "30 * * * *"
assert not dag.catchup
assert dag.max_active_runs == 1
task = dag.task_dict["rebuild_projection"]
assert task.retries == 2
assert task.retry_delay == timedelta(minutes=1)

baseline = None
for hour in (0, 1, 2):
    if hour == 1:
        with GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        ) as driver, driver.session(database=os.environ["NEO4J_DATABASE"]) as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
            # Recovery includes rebuilding constraints on a completely empty graph.
            for record in session.run("SHOW CONSTRAINTS YIELD name RETURN name").data():
                session.run(f"DROP CONSTRAINT `{record['name']}`").consume()
    run = dag.test(logical_date=datetime(2026, 9, 26, hour, tzinfo=UTC))
    assert run.state == "success", run.state
    with GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    ) as driver, driver.session(database=os.environ["NEO4J_DATABASE"]) as session:
        state = (
            session.run("""MATCH (n) RETURN labels(n) AS labels, properties(n) AS props
                           ORDER BY labels(n)[0], n.id""").data(),
            session.run("""MATCH (a)-[r]->(b)
                           RETURN a.id AS source, type(r) AS type, b.id AS target,
                                  properties(r) AS props ORDER BY type, source, target""").data(),
        )
    if baseline is None:
        baseline = state
    else:
        assert state == baseline, "Recovery or replay changed the logical graph"
