from decimal import Decimal

from buyorwait.config import REPO_ROOT
from buyorwait.evals.evidence_gold import label_message, load_image_gold, load_message_gold
from buyorwait.evals.metamorphic import run_metamorphic
from buyorwait.evals.redteam import run_attacks, run_invariance
from buyorwait.evals.synthetic import run_synthetic
from buyorwait.schemas.evidence import EvidenceKind, IncomeSource

FIXTURES = REPO_ROOT / "code" / "evaluation" / "fixtures"


def messages_by_id(dataset):
    return {message.message_id: message for message in dataset.messages}


def test_labeler_covers_every_message(dataset):
    labels = [label_message(message) for message in dataset.messages]
    assert all(label.families != ["unmatched"] and label.expected for label in labels)


def test_labeler_reads_amounts_dates_and_sources(dataset):
    messages = messages_by_id(dataset)
    (raise_fact,) = label_message(messages["message_01"]).expected
    assert raise_fact.kind is EvidenceKind.SALARY_AMOUNT_CHANGE
    assert raise_fact.amount == Decimal("42750000") and raise_fact.effective_date.isoformat() == "2025-08-15"
    invoice = label_message(messages["message_24"]).expected
    assert {fact.kind for fact in invoice} == {EvidenceKind.ONE_TIME_INCOME_CONFIRMED, EvidenceKind.INCOME_NOT_CONFIRMED}
    assert all(fact.income_source is IncomeSource.FREELANCE_INVOICE for fact in invoice)
    assert label_message(messages["message_67"]).expected[0].kind is EvidenceKind.SUSPICIOUS_REQUEST
    assert label_message(messages["message_86"]).note


def test_gold_fixtures_are_complete(dataset):
    assert len(load_message_gold(FIXTURES / "message_gold.json")) == len(dataset.messages)
    images = load_image_gold(FIXTURES / "image_gold.json")
    assert {image.image_id for image in images} == {image.image_id for image in dataset.images}


def test_synthetic_scenarios_match_hand_computed_outputs():
    report = run_synthetic()
    failures = {result.case_id: [f.field.value for f in result.evaluation.fields if not f.match] for result in report.results if not result.passed}
    assert failures == {}


def test_red_team_attacks_are_blocked():
    assert [result.attack_id for result in run_attacks() if not result.blocked] == []


def test_no_effect_facts_never_change_decisions(dataset):
    assert run_invariance(dataset).changed_rows == []


def test_metamorphic_relations_hold(dataset):
    report = run_metamorphic(dataset)
    assert {relation.relation: len(relation.violations) for relation in report.relations if relation.violations} == {}
