
# Copyright 2025 Pokee AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.



import asyncio
import hashlib
import json
import logging
import os
import random
import re
import shutil
import tempfile
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai.types import GenerateContentConfig
from pydantic import BaseModel, Field

from reward.reward_utils import (
    compute_process_reward,
    extract_answer,
    matches_pattern,
    preprocess_text,
)

logger = logging.getLogger("reward_func")
logger.setLevel(logging.INFO)

load_dotenv()

# Cache configuration
CACHE_DIR = "gemini_cache"
CACHE_FILE_WITH_GTS = os.path.join(CACHE_DIR, "gemini_cache_with_gts.json")
CACHE_FILE_NO_GTS = os.path.join(CACHE_DIR, "gemini_cache_no_gts.json")
BATCH_SAVE_INTERVAL = 10000  # Save cache every 10,000 entries

# In-memory caches
_gemini_cache_with_gts: Dict[str, float] = {}
_gemini_cache_no_gts: Dict[str, float] = {}

# Cache hit/miss tracking
_cache_hits = 0
_cache_misses = 0

_genai_client = None


class SerializableRewardError(Exception):
    """Serializable error for Ray workers"""

    def __init__(self, message: str, error_type: str = "RewardError"):
        self.message = message
        self.error_type = error_type
        super().__init__(self.message)


def ensure_cache_dir():
    """Ensure the cache directory exists"""
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)


def generate_cache_key(
    question: str, answer: str, ground_truth: Optional[str] = None
) -> str:
    """Generate a cache key for the given parameters"""
    # Normalize inputs to ensure consistent keys
    question_norm = question.strip().lower()
    answer_norm = answer.strip().lower()

    if ground_truth is not None:
        ground_truth_norm = ground_truth.strip().lower()
        key_string = f"{question_norm}|||{answer_norm}|||{ground_truth_norm}"
    else:
        key_string = f"{question_norm}|||{answer_norm}"

    # Use hash to create a shorter, consistent key
    return hashlib.md5(key_string.encode("utf-8")).hexdigest()


def load_cache_from_file(cache_file: str) -> Dict[str, float]:
    """Load cache from JSON file with validation and corrupted-file backup."""
    if not os.path.exists(cache_file):
        logger.info(
            f"Cache file {cache_file} does not exist, starting with empty cache"
        )
        return {}

    try:
        with open(cache_file, "r") as f:
            cache_data = json.load(f)
            if not isinstance(cache_data, dict):
                logger.warning(
                    f"Cache file {cache_file} contains invalid data, starting with empty cache"
                )
                return {}
            logger.info(
                f"Loaded cache with {len(cache_data)} entries from {cache_file}"
            )
            return cache_data
    except json.JSONDecodeError as e:
        logger.error(
            f"Cache file {cache_file} is corrupted (JSON error: {e}), starting with empty cache"
        )
        backup_file = f"{cache_file}.corrupted"
        try:
            shutil.copy2(cache_file, backup_file)
            logger.info(f"Backed up corrupted cache to {backup_file}")
        except Exception as backup_e:
            logger.error(f"Failed to backup corrupted cache: {backup_e}")
        return {}
    except Exception as e:
        logger.error(
            f"Error loading cache from {cache_file}: {e}, starting with empty cache"
        )
        return {}


def save_cache_to_file(cache_file: str, data: Dict[str, float]) -> bool:
    """Atomically save dict data to JSON file."""
    if not data:
        return True

    ensure_cache_dir()

    try:
        temp_fd, temp_path = tempfile.mkstemp(
            suffix=".tmp", prefix="cache_", dir=os.path.dirname(cache_file) or None
        )
        try:
            with os.fdopen(temp_fd, "w") as temp_file:
                json.dump(data, temp_file, indent=2)
            shutil.move(temp_path, cache_file)
            logger.info(
                f"Successfully saved cache with {len(data)} entries to {cache_file}"
            )
            return True
        except Exception as e:
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            raise e
    except Exception as e:
        logger.error(f"Error saving cache to {cache_file}: {e}")
        return False


def get_cached_score(
    question: str, answer: str, ground_truth: Optional[str] = None
) -> Optional[float]:
    """Get cached score if available"""
    global _cache_hits, _cache_misses
    cache_key = generate_cache_key(question, answer, ground_truth)

    if ground_truth is not None:
        result = _gemini_cache_with_gts.get(cache_key)
    else:
        result = _gemini_cache_no_gts.get(cache_key)

    # Track cache hit/miss statistics
    if result is not None:
        _cache_hits += 1
    else:
        _cache_misses += 1

    return result


def set_cached_score(
    question: str, answer: str, score: float, ground_truth: Optional[str] = None
):
    """Set cached score and conditionally save to file"""
    cache_key = generate_cache_key(question, answer, ground_truth)
    # logger.debug(f"Setting cached score for {cache_key} with score {score}")
    if ground_truth is not None:
        _gemini_cache_with_gts[cache_key] = score
        cache_size = len(_gemini_cache_with_gts)
        if cache_size % BATCH_SAVE_INTERVAL == 0:
            logger.info(f"Batch saving cache with ground truth at {cache_size} entries")
            save_cache_to_file(CACHE_FILE_WITH_GTS, _gemini_cache_with_gts)
    else:
        _gemini_cache_no_gts[cache_key] = score
        cache_size = len(_gemini_cache_no_gts)
        if cache_size % BATCH_SAVE_INTERVAL == 0:
            logger.info(
                f"Batch saving cache without ground truth at {cache_size} entries"
            )
            save_cache_to_file(CACHE_FILE_NO_GTS, _gemini_cache_no_gts)


def initialize_caches():
    """Initialize caches by loading from files"""
    global _gemini_cache_with_gts, _gemini_cache_no_gts

    _gemini_cache_with_gts = load_cache_from_file(CACHE_FILE_WITH_GTS)
    logger.info(f"Loaded {len(_gemini_cache_with_gts)} cached scores with ground truth")

    _gemini_cache_no_gts = load_cache_from_file(CACHE_FILE_NO_GTS)
    logger.info(
        f"Loaded {len(_gemini_cache_no_gts)} cached scores without ground truth"
    )


def get_cache_hit_rate() -> Dict[str, int]:
    """Get cache hit/miss statistics"""
    global _cache_hits, _cache_misses
    total_requests = _cache_hits + _cache_misses
    hit_rate = (_cache_hits / total_requests * 100) if total_requests > 0 else 0.0
    return {
        "cache_hits": _cache_hits,
        "cache_misses": _cache_misses,
        "total_requests": total_requests,
        "hit_rate_percent": round(hit_rate, 2),
    }


def reset_cache_stats():
    """Reset cache hit/miss statistics"""
    global _cache_hits, _cache_misses
    _cache_hits = 0
    _cache_misses = 0


def create_randomized_indices(
    length: int, seed: Optional[int] = None
) -> Tuple[List[int], List[int]]:
    """
    Create randomized indices for data processing while preserving original order.

    Args:
        length: Length of the data to be processed
        seed: Optional random seed for reproducibility

    Returns:
        Tuple of (randomized_indices) where:
        - randomized_indices: indices in random order for processing
    """
    if seed is not None:
        random.seed(seed)

    # Create list of indices
    indices = list(range(length))
    # Randomize the order
    randomized_indices = indices.copy()
    random.shuffle(randomized_indices)

    return randomized_indices


def reorder_results(results: List, randomized_indices: List[int]) -> List:
    """
    Reorder results back to original order using randomized indices.

    Args:
        results: Results in randomized order

    Returns:
        Results in original order
    """
    # Create reverse mapping to restore original order
    results_in_original_order = [0] * len(results)
    for i, original_idx in enumerate(randomized_indices):
        results_in_original_order[original_idx] = results[i]
    return results_in_original_order


def exact_match_score(
    question: str,
    answer: str,
    ground_truth: str,
    data_source: str,
    solution_str: str,
    use_answer_verification: bool,
) -> float:
    """Compute the score between the solution string and ground truth

    Args:
        solution_str: The solution string to evaluate
        ground_truth: The ground truth string
        process_reward: Whether to process tool usage rewards

    Returns:
        float: The computed score
    """
    ground_truth = ground_truth.lower()
    ground_truths = ground_truth.split("<|answer_split|>")
    answer_content = preprocess_text(answer)

    for gt in ground_truths:
        # Preprocess the ground truth
        gt = preprocess_text(gt)

        if gt == answer_content:
            return 1.0

    return 0.0


def f1_score(
    question: str,
    answer: str,
    ground_truth: str,
    data_source: str,
    solution_str: str,
    use_answer_verification: bool,
) -> float:
    """Compute the score between the solution string and ground truth

    Args:
        solution_str: The solution string to evaluate
        ground_truth: The ground truth string
        process_reward: Whether to process tool usage rewards

    Returns:
        float: The computed score
    """
    if (
        exact_match_score(
            question,
            answer,
            ground_truth,
            data_source,
            solution_str,
            use_answer_verification,
        )
        == 1.0
    ):
        return 1.0
    ground_truth = ground_truth.lower()
    ground_truths = ground_truth.split("<|answer_split|>")
    answer_content = preprocess_text(answer)

    max_score = 0.0

    for gt in ground_truths:
        # Preprocess the ground truth
        gt = preprocess_text(gt)

        # Tokenize the answer and ground truth
        pred_tokens = set(answer_content.split())
        gt_tokens = set(gt.split())

        pre_tokens_len, gt_tokens_len = len(pred_tokens), len(gt_tokens)

        if gt_tokens_len == 0 or pre_tokens_len == 0:  # Avoid division by zero
            continue

        # Calculate the number of common tokens
        common_tokens_len = len(pred_tokens & gt_tokens)

        # Calculate precision and recall
        precision = common_tokens_len / pre_tokens_len
        recall = common_tokens_len / gt_tokens_len

        # Calculate F1 score
        if precision + recall > 0:  # Avoid division by zero
            f1 = 2 * (precision * recall) / (precision + recall)
            max_score = max(max_score, f1)

    return max_score


def get_google_genai_client():
    """Get or create the global GenAI client instance."""
    global _genai_client
    if _genai_client is None:
        try:
            _genai_client = genai.client.Client(
                api_key=os.getenv("GEMINI_API_KEY"),
                vertexai=False,
            )
        except Exception as e:
            logger.error(f"Failed to create GenAI client: {str(e)}")
            raise SerializableRewardError(f"GenAI client creation failed: {str(e)}")
    return _genai_client


class LLMJudgement(BaseModel):
    rationale: str = Field(..., description="The rationale for the judgement")
    correct: bool = Field(..., description="Whether the predicted answer is correct")


SYSTEM_INSTRUCTION = """You will be given a question and its ground truth answer list where each item can be a ground truth answer. Provided a pred_answer, you need to judge if the pred_answer correctly answers the question based on the ground truth answer list.
You should first give your rationale for the judgement, and then give your judgement result (i.e., correct or incorrect).

Here is the criteria for the judgement:
1. The pred_answer doesn't need to be exactly the same as any of the ground truth answers, but should be semantically same for the question.
2. Each item in the ground truth answer list can be viewed as a ground truth answer for the question, and the pred_answer should be semantically same to at least one of them.
"""

USER_PROMPT = """question: {question}\n ground truth answers: {gt_answer}\n pred_answer: {pred_answer}"""


def format_ground_truths(ground_truths: List[str]) -> str:
    """Format ground truth list as numbered items"""
    if len(ground_truths) == 1:
        return ground_truths[0].strip()

    formatted = []
    for i, gt in enumerate(ground_truths, 1):
        formatted.append(f"{i}. {gt.strip()}")
    return "\n".join(formatted)


async def reward_func_async(
    data_source,
    question,
    solution_str,
    ground_truth,
    use_answer_verification: bool,
    reward_types: list[str] = ["gemini_mbe"],
) -> Dict[str, float]:
    assert data_source in [
        "nq",
        "2wiki",
        "Bamboogle",
        "hotpotqa",
        "musique",
        "tq",
        "popqa",
        "hle_text_only_2000",
        "gaia_text_only",
        "genqav4",
        "browsecomp_test",
    ]
    reward_type_to_func = {
        "process_reward": compute_process_reward_async,
        "format_reward": matches_pattern_async,
        "f1": f1_score_async,
        "em": exact_match_score_async,
        "gemini_mbe": gemini_mbe_score_async,
    }
    assert all(
        reward_type in reward_type_to_func.keys() for reward_type in reward_types
    ), (
        f"Invalid reward_types: {reward_types}. Keys should be in {list(reward_type_to_func.keys())}"
    )

    reward_type_to_reward = {reward_type: 0 for reward_type in reward_types}

    solution_str = solution_str.lower()
    answer = extract_answer(solution_str)

    # Do these checks once at the beginning
    if answer is None:
        return reward_type_to_reward

    tasks = [
        reward_type_to_func[reward_type](
            question,
            answer,
            ground_truth,
            data_source,
            solution_str,
            use_answer_verification,
        )
        for reward_type in reward_types
    ]
    results = await asyncio.gather(*tasks)
    for reward_type, result in zip(reward_types, results):
        reward_type_to_reward[reward_type] = result

    return reward_type_to_reward


async def reward_func_batch_async(
    data_sources,
    list_of_questions,
    solution_strs,
    ground_truths,
    use_answer_verification: bool,
    reward_types: list[str] = ["gemini_mbe"],
    batch_size: int = 50,
    delay_between_batches: float = 0.1,
) -> List[Dict[str, float]]:
    assert (
        len(data_sources)
        == len(list_of_questions)
        == len(solution_strs)
        == len(ground_truths)
    ), "Length of data_sources, solution_strs, and ground_truths must be the same."

    all_results = []
    total_items = len(data_sources)

    # Process in batches to avoid overwhelming the API
    for i in range(0, total_items, batch_size):
        logging.info(
            f"Processing batch {i} of {total_items} in reward_func_batch_async"
        )
        batch_end = min(i + batch_size, total_items)
        batch_data_sources = data_sources[i:batch_end]
        batch_questions = list_of_questions[i:batch_end]
        batch_solution_strs = solution_strs[i:batch_end]
        batch_ground_truths = ground_truths[i:batch_end]

        tasks = []
        for data_source, question, solution_str, ground_truth in zip(
            batch_data_sources,
            batch_questions,
            batch_solution_strs,
            batch_ground_truths,
        ):
            tasks.append(
                reward_func_async(
                    data_source=data_source,
                    question=question,
                    solution_str=solution_str,
                    ground_truth=ground_truth,
                    reward_types=reward_types,
                    use_answer_verification=use_answer_verification,
                )
            )

        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        all_results.extend(batch_results)

        # Add delay between batches to respect rate limits
        if batch_end < total_items:
            await asyncio.sleep(delay_between_batches)

    return all_results


def reward_func_batch_sync(
    data_sources,
    list_of_questions,
    solution_strs,
    ground_truths,
    use_answer_verification: bool,
    reward_types: list[str] = ["gemini_mbe"],
    batch_size: int = 500,
    delay_between_batches: float = 0.5,
    randomize_order: bool = True,
    random_seed: Optional[int] = 42,
):
    # Reset cache statistics before starting
    reset_cache_stats()

    # Handle randomization if requested
    if randomize_order:
        length = len(data_sources)
        randomized_indices = create_randomized_indices(length, random_seed)

        # Reorder input data according to randomized indices
        data_sources = [data_sources[i] for i in randomized_indices]
        list_of_questions = [list_of_questions[i] for i in randomized_indices]
        solution_strs = [solution_strs[i] for i in randomized_indices]
        ground_truths = [ground_truths[i] for i in randomized_indices]

        # logger.debug(f"Randomized order for processing (seed={random_seed})")

    result = asyncio.run(
        reward_func_batch_async(
            data_sources,
            list_of_questions,
            solution_strs,
            ground_truths,
            use_answer_verification,
            reward_types,
            batch_size,
            delay_between_batches,
        )
    )

    # Reorder results back to original order if randomization was used
    if randomize_order:
        result = reorder_results(result, randomized_indices)
        # logger.debug("Results reordered to original sequence")

    # Print cache hit rate statistics
    cache_stats = get_cache_hit_rate()
    logger.info("\n=== Cache Hit Rate for reward_func_batch_sync ===")
    logger.info(f"Total requests: {cache_stats['total_requests']}")
    logger.info(f"Cache hits: {cache_stats['cache_hits']}")
    logger.info(f"Cache misses: {cache_stats['cache_misses']}")
    logger.info(f"Hit rate: {cache_stats['hit_rate_percent']}%")
    logger.info("=" * 50)

    return result


async def compute_process_reward_async(
    question: str,
    answer: str,
    ground_truth: str,
    data_source: str,
    solution_str: str,
    use_answer_verification: bool,
) -> float:
    return float(compute_process_reward(solution_str))


async def matches_pattern_async(
    question: str,
    answer: str,
    ground_truth: str,
    data_source: str,
    solution_str: str,
    use_answer_verification: bool,
) -> float:
    return float(matches_pattern(solution_str, use_answer_verification))


async def exact_match_score_async(
    question: str,
    answer: str,
    ground_truth: str,
    data_source: str,
    solution_str: str,
    use_answer_verification: bool,
) -> float:
    """Compute the score between the solution string and ground truth

    Args:
        solution_str: The solution string to evaluate
        ground_truth: The ground truth string
        process_reward: Whether to process tool usage rewards

    Returns:
        float: The computed score
    """
    return exact_match_score(
        question,
        answer,
        ground_truth,
        data_source,
        solution_str,
        use_answer_verification,
    )


async def f1_score_async(
    question: str,
    answer: str,
    ground_truth: str,
    data_source: str,
    solution_str: str,
    use_answer_verification: bool,
) -> float:
    """Compute the score between the solution string and ground truth

    Args:
        solution_str: The solution string to evaluate
        ground_truth: The ground truth string
        process_reward: Whether to process tool usage rewards

    Returns:
        float: The computed score
    """
    return f1_score(
        question,
        answer,
        ground_truth,
        data_source,
        solution_str,
        use_answer_verification,
    )


def extract_retry_delay_from_error(error_str: str) -> Optional[float]:
    """
    Extract retry delay from Gemini API error response.
    Returns the delay in seconds if found, None otherwise.
    """
    if "RESOURCE_EXHAUSTED" in error_str and "retryDelay" in error_str:
        # check retry delay for gemini models
        # Look for retryDelay pattern in the error message
        retry_delay_match = re.search(r"'retryDelay': '(\d+(?:\.\d+)?)s'", error_str)
        if retry_delay_match:
            return float(retry_delay_match.group(1))

        # Alternative pattern matching
        retry_delay_match = re.search(
            r'retryDelay["\']?\s*:\s*["\']?(\d+(?:\.\d+)?)s', error_str
        )
        if retry_delay_match:
            return float(retry_delay_match.group(1))

    return None


async def call_gemini_2_5_flash_lite_async(
    client, prompt, system_instruction
) -> Tuple[Optional[LLMJudgement], str]:
    error_str = ""
    try:
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=GenerateContentConfig(
                response_modalities=["TEXT"],
                response_mime_type="application/json",
                system_instruction=system_instruction,
                response_schema=LLMJudgement,
            ),
        )
        judgement = LLMJudgement.model_validate_json(response.text)
    except Exception as e:
        logger.warning(f"Failed to obtain LLMJudgement: {e}")
        error_str = str(e)
        judgement = None
    return judgement, error_str


def get_retry_delay(try_cnt: int, error_str: str) -> Optional[float]:
    # Calculate retry delay
    retry_delay = extract_retry_delay_from_error(error_str)
    if retry_delay is None:
        # Fallback to exponential backoff: 1s, 2s, 4s, 8s, etc.
        retry_delay = min(4**try_cnt, 60)  # Cap at 60 seconds
    return retry_delay


async def call_llm_with_retry_async(
    llm_call_func,
    client,
    prompt,
    system_instruction,
    max_retries,
) -> Tuple[Optional[float], str]:
    """Return a judged score, or None when no valid judgement was obtained."""
    func_name = getattr(llm_call_func, "__name__", "llm_call")
    for try_cnt in range(max_retries + 1):
        judgement = None
        try:
            judgement, error_str = await llm_call_func(
                client, prompt, system_instruction
            )
            if not isinstance(judgement, LLMJudgement):
                if try_cnt == max_retries:
                    logger.error(
                        f"[Error] when calling {func_name}: attempt {try_cnt + 1}/{max_retries + 1}, judgement is {judgement}"
                    )
                    return (
                        None,
                        f"Failed to obtain LLMJudgement after {max_retries + 1} attempts",
                    )
                retry_delay = get_retry_delay(try_cnt, error_str)
                logger.warning(
                    f"[Warning] when calling {func_name}: attempt {try_cnt + 1}/{max_retries + 1} error: {error_str}. Try next after {retry_delay}s"
                )
                await asyncio.sleep(retry_delay)
                continue
            return (
                1.0 if judgement.correct else 0.0,
                f"LLMJudgement: {judgement.rationale}",
            )
        except Exception as e:
            if try_cnt == max_retries:
                logger.error(
                    f"[Error] when calling {func_name}: attempt {try_cnt + 1}/{max_retries + 1} error: {e}"
                )
                return (
                    None,
                    f"Failed to obtain LLMJudgement after {max_retries + 1} attempts: {e}",
                )
            retry_delay = get_retry_delay(try_cnt, str(e))
            logger.warning(
                f"[Warning] when calling {func_name} an exception has been raised: attempt {try_cnt + 1}/{max_retries + 1} error: {e}. Try next after {retry_delay}s"
            )

            await asyncio.sleep(retry_delay)

    # This should never be reached, but just in case
    return None, "Unexpected end of retry loop"


async def gemini_mbe_score_async(
    question: str,
    answer: str,
    ground_truth: str,
    data_source: str,
    solution_str: str,
    use_answer_verification: bool,
) -> float:
    if (
        exact_match_score(
            question,
            answer,
            ground_truth,
            data_source,
            solution_str,
            use_answer_verification,
        )
        == 1.0
    ):
        return 1.0

    # Check cache first
    cached_score = get_cached_score(question, answer, ground_truth)
    if cached_score is not None:
        # logger.debug(f"Cache hit for gemini_mbe_score_async: {cached_score}")
        return cached_score

    ground_truth_lower = ground_truth.lower()
    ground_truth_list = ground_truth_lower.split("<|answer_split|>")
    ground_truths = format_ground_truths(ground_truth_list)
    client = get_google_genai_client()
    score, rationale = await call_llm_with_retry_async(
        llm_call_func=call_gemini_2_5_flash_lite_async,
        client=client,
        prompt=USER_PROMPT.format(
            question=question, gt_answer=ground_truths, pred_answer=answer
        ),
        system_instruction=SYSTEM_INSTRUCTION,
        max_retries=3,
    )

    # Preserve the zero-score fallback without caching a failed judge request.
    # A later evaluation can retry; genuine incorrect judgements remain cached.
    if score is None:
        return 0.0

    # Cache the result
    set_cached_score(question, answer, score, ground_truth)
    # logger.debug(f"Cached gemini_mbe_score_async result: {score}")

    return score


def test_reward_func_batch_sync():
    import time

    num_samples = 500
    data_sources = ["nq"] * num_samples
    question = [
        "In Valentina Re's contribution to the 2017 book “World Building: Transmedia, Fans, Industries”, what horror movie does the author cite as having popularized metalepsis between a dream world and reality? Use the complete name with article if any."
    ] * num_samples
    pred_answer = ["<answer>Gone Girl</answer>"] * num_samples
    gts = ["A Nightmare on Elm Street"] * num_samples
    start_time = time.time()

    # Use smaller batch size and longer delay for Gemini API
    results = reward_func_batch_sync(
        data_sources,
        question,
        pred_answer,
        gts,
        reward_types=["gemini_mbe"],
        use_answer_verification=False,
        batch_size=100,  # Small batch size
        delay_between_batches=0.5,  # 500ms delay between batches
        randomize_order=True,  # Enable randomization
        random_seed=42,  # Fixed seed for reproducibility
    )

    end_time = time.time()
    logging.info(f"Time taken: {end_time - start_time} seconds")
    logging.info(f"Processed {len(results)} samples")
    logging.info(f"First few results: {results[:3]}")


# Initialize caches on module load
initialize_caches()

if __name__ == "__main__":
    test_reward_func_batch_sync()
