"""An unavailable judge must not leave a permanent incorrect-answer label."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from reward import reward_score


def judgement(correct):
    return SimpleNamespace(text=json.dumps({"rationale": "Checked the answer.", "correct": correct}))


@pytest.fixture
def judge_client(monkeypatch):
    generate = AsyncMock()
    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(reward_score, "_genai_client", client)
    monkeypatch.setattr(reward_score, "_gemini_cache_with_gts", {})
    monkeypatch.setattr(reward_score, "_cache_hits", 0)
    monkeypatch.setattr(reward_score, "_cache_misses", 0)
    monkeypatch.setattr(reward_score.asyncio, "sleep", AsyncMock())
    return generate


def score_answer(answer="NYC"):
    return asyncio.run(
        reward_score.reward_func_async(
            data_source="nq",
            question="Which city is nicknamed the Big Apple?",
            solution_str=f"<answer>{answer}</answer>",
            ground_truth="New York City",
            use_answer_verification=False,
        )
    )["gemini_mbe"]


@pytest.mark.parametrize("failure", ["request_error", "invalid_json", "invalid_judgement"])
def test_exhausted_retries_do_not_poison_the_reward_cache(judge_client, failure):
    failed_response = {
        "request_error": RuntimeError("Service temporarily unavailable"),
        "invalid_json": SimpleNamespace(text="truncated response"),
        "invalid_judgement": SimpleNamespace(text='{"rationale": "No verdict", "correct": null}'),
    }[failure]
    judge_client.side_effect = [failed_response] * 4 + [judgement(True)]

    first_score = score_answer()
    cache_after_failure = dict(reward_score._gemini_cache_with_gts)
    second_score = score_answer()
    third_score = score_answer()

    assert (first_score, second_score, third_score) == (0.0, 1.0, 1.0)
    assert cache_after_failure == {}
    assert list(reward_score._gemini_cache_with_gts.values()) == [1.0]
    assert judge_client.await_count == 5


@pytest.mark.parametrize("correct", [False, True])
def test_completed_judgements_are_cached_including_zero(judge_client, correct):
    judge_client.return_value = judgement(correct)

    assert score_answer() == float(correct)
    assert score_answer() == float(correct)
    assert list(reward_score._gemini_cache_with_gts.values()) == [float(correct)]
    judge_client.assert_awaited_once()


def test_exact_match_still_bypasses_the_judge(judge_client):
    assert score_answer("New York City") == 1.0
    judge_client.assert_not_awaited()
