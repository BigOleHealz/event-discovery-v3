ARG AIRFLOW_VERSION=3.3.1
FROM apache/airflow:${AIRFLOW_VERSION}

ARG AIRFLOW_VERSION

COPY requirements.txt /opt/airflow/requirements.txt
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" \
    --requirement /opt/airflow/requirements.txt

COPY dags /opt/airflow/dags
