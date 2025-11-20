# Copyright 2024 Bytedance Ltd. and/or its affiliates
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

"""
Generate responses given a dataset of prompts
"""
import json
from collections import defaultdict
from reward.reward_score import reward_func_batch_sync
import os
import torch
import hydra
import numpy as np
import ray

os.environ["NCCL_DEBUG"] = "WARN"
os.environ["TOKENIZERS_PARALLELISM"] = "true"
# os.environ['TORCH_COMPILE_DISABLE'] = '1'

from pprint import pprint

import pandas as pd
from omegaconf import OmegaConf

from verl import DataProto
from verl.protocol import pad_dataproto_to_divisor, unpad_dataproto
from verl.single_controller.ray import RayClassWithInitArgs, RayResourcePool, RayWorkerGroup
from verl.utils import hf_tokenizer
from verl.utils.fs import copy_to_local
from verl.utils.hdfs_io import makedirs
from verl.utils.model import compute_position_id_with_mask
from verl.workers.fsdp_workers import ActorRolloutRefWorker

SUMMARIZATION_SYSTEM_PROMPT = """
You are an expert in summarizing agent's research trace.

Input: a user question and the full record of an agent's research trace.

Goal: produce a complete summary that faithfully summarizes the agent's research trace and provides all the findings obtained from online sources and used by the agent to reach the answer.

Instructions:
1. If the research trace contains one or more <answer>...</answer> tags, extract the text from the **last** such tag as the agent's final answer. If no <answer> tag is found, output: <answer>No answer found.</answer>.
2. After the <answer> block, summarize the research trace as follows:
    - **Findings**: include the online sources the agent used to form the final answer. For each, provide the URL and the full original content the agent relied on.  
    - **Reasoning**: explain, step by step, how the agent interpreted and combined the evidence to reach its conclusion. Do not add your own reasoning or analysis. Just faithfully summarize the agent's reasoning.

Output format (must follow exactly):
<answer>
AGENT'S ANSWER HERE
</answer>
<research_trace_summary>
findings:
- url: URL1
    content: FULL_CONTENT_1
- url: URL2
    content: FULL_CONTENT_2
...
reasoning:
- STEP 1: 
    REASONING_1
- STEP 2:
    REASONING_2
...
</research_trace_summary>
"""


GENERATE_ANSWER_SYSTEM_PROMPT = """
You are an expert in aggregating research outcomes to produce the most reliable final answer.

Input: a user question and multiple pairs of <answer> and <research_trace_summary> entries, each representing one agent's independent research trace.

Goal: determine the single most accurate and well-supported final answer.

Instructions:
1. For each (answer, research_trace_summary) pair, you should read carefully and explain if
    - the reasoning steps in the research trace summary are coherent and logical and clearly lead to the answer.
    - the reasoning steps are supported by the web findings in the research trace summary.
    - the reasoning steps show that the answer addresses every aspect of the question.
2. Compare the pairs based on the criteria above to produce the best final answer:
    - If there is only one pair that meets all the criteria, output the answer.
    - If there are multiple pairs that meet all the criteria and the answers are the same semantically, output any of the answers.
    - If there are multiple pairs that meet all the criteria and the answers are not the same semantically, output the answer that you think is the most accurate.
    - If none of the answers meet these criteria, you should reason from your own general knowledge and the research trace summaries to produce the best possible answer. Your answer should directly answer the question.
    - If you can not determine the answer based on your own knowledge and the research trace summaries, or you think there is no answer to the question, output: <answer>I have performed research but I can not find the answer to the question.</answer>

Output format (must follow exactly):
<think>
Analysis of (answer, research_trace_summary) pair 1: ...
Analysis of (answer, research_trace_summary) pair 2: ...
...
Analysis to determine the best final answer: ...
</think>
<answer>
YOUR BEST ANSWER HERE
</answer>
"""

def construct_question_response(question: str, response: str) -> str:
    return f"""
    Question: {question}
    Research trace:  #### Research trace starts from here #### {response} #### Research trace ends here ####
    """

def construct_question_and_all_responses(question: str, all_responses: list[str]) -> str:
    rtn_str = f"""
    Question: {question}

    #### All research traces start from here ####
    """
    for i, response in enumerate(all_responses):
        rtn_str += f"""
        Research trace {i + 1}: {response}
        """
    rtn_str += f"""
    #### All research traces end here ####
    """
    return rtn_str

@hydra.main(config_path="config", config_name="generation", version_base=None)
def main(config):
    run_generation(config)


def run_generation(config) -> None:
    if not ray.is_initialized():
        # this is for local ray cluster
        default_runtime_env = {"env_vars": {"TOKENIZERS_PARALLELISM": "true", "NCCL_DEBUG": "WARN"}}
        ray_init_kwargs = config.ray_kwargs.get("ray_init", {})
        runtime_env_kwargs = ray_init_kwargs.get("runtime_env", {})
        runtime_env = OmegaConf.merge(default_runtime_env, runtime_env_kwargs)
        ray_init_kwargs = OmegaConf.create({**ray_init_kwargs, "runtime_env": runtime_env})
        print(f"ray init kwargs: {ray_init_kwargs}")
        ray.init(**OmegaConf.to_container(ray_init_kwargs))

    ray.get(main_task.remote(config))


def generate(chat_lst, num_batch, config_batch_size, config_prompt_len, tokenizer, apply_chat_template_kwargs, wg):
    output_lst = []
    for batch_idx in range(num_batch):
        print(f"[{batch_idx + 1}/{num_batch}] Start to process.")
        batch_chat_lst = chat_lst[batch_idx * config_batch_size : (batch_idx + 1) * config_batch_size]
        inputs = tokenizer.apply_chat_template(
            batch_chat_lst,
            add_generation_prompt=True,
            padding=True,
            truncation=True,
            max_length=config_prompt_len,
            return_tensors="pt",
            return_dict=True,
            tokenize=True,
            **apply_chat_template_kwargs,
        )
        input_ids = inputs["input_ids"]
        attention_mask = inputs["attention_mask"]

        position_ids = compute_position_id_with_mask(attention_mask)
        batch_dict = {"input_ids": input_ids, "attention_mask": attention_mask, "position_ids": position_ids}
        data = DataProto.from_dict(batch_dict)
        data_padded, pad_size = pad_dataproto_to_divisor(data, wg.world_size)

        # START TO GENERATE FOR n_samples TIMES
        print(f"[{batch_idx + 1}/{num_batch}] Start to generate.")
        output_padded = wg.generate_sequences(data_padded)
        output = unpad_dataproto(output_padded, pad_size=pad_size)

        output_texts = []
        for i in range(len(output)):
            data_item = output[i]
            prompt_length = data_item.batch["prompts"].shape[-1]
            valid_response_length = data_item.batch["attention_mask"][prompt_length:].sum()
            valid_response_ids = data_item.batch["responses"][:valid_response_length]
            response_str = tokenizer.decode(valid_response_ids, skip_special_tokens=True)
            output_texts.append(response_str)
        output_lst.extend(output_texts)
    
    return output_lst
    

@ray.remote(num_cpus=1)
def main_task(config):
    val_results_path = config.data.val_results_path
    val_results_file_name = config.data.val_results_file_name
    pprint(OmegaConf.to_container(config, resolve=True))  # resolve=True will eval symbol values
    OmegaConf.resolve(config)

    local_path = copy_to_local(config.model.path)
    trust_remote_code = config.data.get("trust_remote_code", False)
    tokenizer = hf_tokenizer(local_path, trust_remote_code=trust_remote_code)

    config_batch_size = config.data.batch_size
    config_prompt_len = config.rollout.prompt_length
    config_response_len = config.rollout.response_length
    apply_chat_template_kwargs = config.data.get("apply_chat_template_kwargs", {})
    tokenizer.padding_side = "left"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    ray_cls_with_init = RayClassWithInitArgs(cls=ray.remote(ActorRolloutRefWorker), config=config, role="rollout")
    resource_pool = RayResourcePool(process_on_nodes=[config.trainer.n_gpus_per_node] * config.trainer.nnodes)
    wg = RayWorkerGroup(
        resource_pool=resource_pool,
        ray_cls_with_init=ray_cls_with_init,
        device_name=config.trainer.device,
    )
    wg.init_model()

    if not os.path.exists(f"data_source_question_to_info_{val_results_file_name}"):

        # load val_results
        with open(os.path.join(val_results_path, val_results_file_name), "r") as f:
            val_results = json.load(f)
        data_source_lst = val_results.keys()

        chat_lst = []

        for data_source in data_source_lst:
            for data_item in val_results[data_source]["data_list"]:
                data_item_to_process = []
                data_item_to_process.append({"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT})
                data_item_to_process.append({"role": "user", "content": construct_question_response(data_item["question"], data_item["response"])})
                chat_lst.append(data_item_to_process)

        total_samples = len(chat_lst)
        num_batch = -(-total_samples // config_batch_size)
        
        output_lst = generate(chat_lst, num_batch, config_batch_size, config_prompt_len, tokenizer, apply_chat_template_kwargs, wg)

        i = 0
        for key in val_results.keys():
            for data_item in val_results[key]["data_list"]:
                data_item["summarized_response"] = output_lst[i]
                i += 1

        # Group responses by (data_source, question) pair
        data_source_question_to_info = {}
        for key in val_results.keys():
            for data_item in val_results[key]["data_list"]:
                # Create unique key from data_source and question
                unique_key = f"{key}||{data_item['question']}"
                
                if unique_key not in data_source_question_to_info:
                    data_source_question_to_info[unique_key] = {
                        "data_source": key,
                        "question": data_item["question"],
                        "ground truth": data_item["ground truth"],
                        "all_responses": [],
                        "gemini_mbe_scores": [],
                    }
                data_source_question_to_info[unique_key]["all_responses"].append(data_item["summarized_response"])
                data_source_question_to_info[unique_key]["gemini_mbe_scores"].append(data_item["gemini_mbe"])

        # now pass the question and all responses to the model.
        # The model analyzes the question and all summarized responses to generate a answer to the question.

        chat_lst = []
        for unique_key in data_source_question_to_info.keys():
            data_item_to_process = []
            data_item_to_process.append({"role": "system", "content": GENERATE_ANSWER_SYSTEM_PROMPT})
            data_item_to_process.append({"role": "user", "content": construct_question_and_all_responses(
                data_source_question_to_info[unique_key]["question"], 
                data_source_question_to_info[unique_key]["all_responses"]
            )})
            chat_lst.append(data_item_to_process)

        # Recalculate num_batch for the new chat_lst size
        total_samples = len(chat_lst)
        num_batch = -(-total_samples // config_batch_size)
        
        output_lst = generate(chat_lst, num_batch, config_batch_size, config_prompt_len, tokenizer, apply_chat_template_kwargs, wg)

        # Add final answers to the data structure
        for i, unique_key in enumerate(data_source_question_to_info.keys()):
            data_source_question_to_info[unique_key]["final_answer"] = output_lst[i]

        # save to json
        with open(f"data_source_question_to_info_{val_results_file_name}", "w") as f:
            json.dump(data_source_question_to_info, f, indent=4)

        print(f"Grouped responses saved to data_source_question_to_info_{val_results_file_name}")
    
    else:
        with open(f"data_source_question_to_info_{val_results_file_name}", "r") as f:
            data_source_question_to_info = json.load(f)
        output_lst = [data_source_question_to_info[unique_key]["final_answer"] for unique_key in data_source_question_to_info.keys()]

    # Run Gemini-2.5-Flash-lite model to evaluate the performance of the generated answer by comparing it with the ground truth.

    # Build lists in a single iteration to ensure consistency
    data_sources_list = []
    questions_list = []
    ground_truths_list = []
    for unique_key in data_source_question_to_info.keys():
        data_sources_list.append(data_source_question_to_info[unique_key]["data_source"])
        questions_list.append(data_source_question_to_info[unique_key]["question"])
        ground_truths_list.append(data_source_question_to_info[unique_key]["ground truth"])

    rewards = reward_func_batch_sync(
        data_sources=data_sources_list,
        list_of_questions=questions_list,
        solution_strs=output_lst,
        ground_truths=ground_truths_list,
        use_answer_verification=True,
        reward_types=["gemini_mbe"],
    )
    
    # Add gemini_mbe scores for final answers back to the data structure
    for i, unique_key in enumerate(data_source_question_to_info.keys()):
        data_source_question_to_info[unique_key]["gemini_mbe_final_answer"] = rewards[i]["gemini_mbe"]
    
    # save to json
    with open(f"data_source_question_to_info_with_rewards_{val_results_file_name}", "w") as f:
        json.dump(data_source_question_to_info, f, indent=4)
    print(f"Results with rewards saved to data_source_question_to_info_with_rewards_{val_results_file_name}")

    # Compute average gemini_mbe for each data source
    data_source_to_gemini_mbe = defaultdict(list)
    for unique_key, info in data_source_question_to_info.items():
        data_source = info["data_source"]
        # Add score from this data_source/question pair
        if "gemini_mbe_final_answer" in info:
            data_source_to_gemini_mbe[data_source].append(info["gemini_mbe_final_answer"])
        else:
            assert False, "gemini_mbe_final_answer not found for data source: " + data_source
    
    # NEW CODE: Enhanced metrics for data sources
    for data_source, gemini_mbe_list in data_source_to_gemini_mbe.items():
        avg_score = np.mean(gemini_mbe_list)

        # --- Metric 21 (Success Rate) ---
        # Calculates % of scores greater than or equal to 0.8
        success_rate = np.mean([s >= 0.8 for s in gemini_mbe_list])

        print(f"Data Source: {data_source}")
        print(f"  - Avg Gemini MBE: {avg_score:.4f}")
        print(f"  - Success Rate (>=0.8): {success_rate:.2%}")

    # --- Metric 25 (Coverage Rate) ---
    # Checks output_lst for your specific failure string
    coverage = np.mean(["I have performed research but I can not find the answer" not in ans for ans in output_lst])
    print(f"\nGlobal Coverage Rate: {coverage:.2%}")

if __name__ == "__main__":
    main()
