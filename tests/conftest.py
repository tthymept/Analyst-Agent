"""Local Spark for tests. Run with:  uv run pytest

Spark 4 needs Java 17 or 21 (Java 23+ breaks Hadoop). Point SPARK_TEST_JAVA_HOME at one, or keep a
JDK 21 in ~/.jdks/ and it is found automatically.
"""

import glob
import os
import sys
from pathlib import Path

import pytest


def _configure_environment() -> None:
    os.environ.pop("SPARK_HOME", None)          # use the pip pyspark, not a separately installed Spark
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    java_home = os.environ.get("SPARK_TEST_JAVA_HOME")
    if not java_home:
        found = sorted(glob.glob(str(Path.home() / ".jdks" / "jdk-21*")))
        java_home = found[-1] if found else None
    if java_home:
        os.environ["JAVA_HOME"] = java_home


@pytest.fixture(scope="session")
def spark():
    _configure_environment()
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[1]")
        .appName("analyst-agent-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.sql.ansi.enabled", "true")  # same as Databricks serverless
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    yield session
    session.stop()
