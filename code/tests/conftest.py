import pytest

from buyorwait.config import REPO_ROOT
from buyorwait.ingest.lifecycle import build_ledger
from buyorwait.ingest.loaders import Dataset, load_dataset


@pytest.fixture(scope="session")
def dataset() -> Dataset:
    return load_dataset(REPO_ROOT / "dataset")


@pytest.fixture(scope="session")
def ledger(dataset):
    return build_ledger(dataset.events, dataset.profiles, dataset.fx)
