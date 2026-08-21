"""Official-style LongMemEval answer-check prompts and parsing."""

from __future__ import annotations

import re

from shared.llm_client import chat_with_repair

# Judge models sometimes return empty or ambiguous output.  On retry we
# append a corrective instruction and raise temperature above 0 so the model
# does not reproduce the identical bad output (temperature 0 is deterministic).
LONGMEMEVAL_REPAIR_PROMPT = (
    "\n\nYour previous response was empty or not a clear yes/no answer. "
    "Answer with a single word: yes or no."
)


def build_answer_check_prompt(
    task: str,
    question: str,
    answer: str,
    response: str,
    *,
    abstention: bool = False,
) -> str:
    if not abstention:
        if task in {
            "single-session-user",
            "single-session-assistant",
            "multi-session",
        }:
            return (
                "I will give you a question, a correct answer, and a response from a model. "
                "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
                "If the response is equivalent to the correct answer or contains all the intermediate steps "
                "to get the correct answer, you should also answer yes. If the response only contains a subset "
                "of the information required by the answer, answer no. \n\n"
                f"Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
        if task == "temporal-reasoning":
            return (
                "I will give you a question, a correct answer, and a response from a model. "
                "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
                "If the response is equivalent to the correct answer or contains all the intermediate steps "
                "to get the correct answer, you should also answer yes. If the response only contains a subset "
                "of the information required by the answer, answer no. In addition, do not penalize off-by-one "
                "errors for the number of days. If the question asks for the number of days/weeks/months, etc., "
                "and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), "
                "the model's response is still correct. \n\n"
                f"Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
        if task == "knowledge-update":
            return (
                "I will give you a question, a correct answer, and a response from a model. "
                "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
                "If the response contains some previous information along with an updated answer, the response "
                "should be considered as correct as long as the updated answer is the required answer.\n\n"
                f"Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
        if task == "single-session-preference":
            return (
                "I will give you a question, a rubric for desired personalized response, and a response from a model. "
                "Please answer yes if the response satisfies the desired response. Otherwise, answer no. "
                "The model does not need to reflect all the points in the rubric. The response is correct as long "
                "as it recalls and utilizes the user's personal information correctly.\n\n"
                f"Question: {question}\n\nRubric: {answer}\n\nModel Response: {response}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
    return (
        "I will give you an unanswerable question, an explanation, and a response from a model. "
        "Please answer yes if the model correctly identifies the question as unanswerable. "
        "The model could say that the information is incomplete, or some other information is given but "
        "the asked information is not.\n\n"
        f"Question: {question}\n\nExplanation: {answer}\n\nModel Response: {response}\n\n"
        "Does the model correctly identify the question as unanswerable? Answer yes or no only."
    )


def parse_yes_no(text: str) -> bool:
    value = str(text or "").strip().lower()
    has_yes = bool(re.search(r"\byes\b", value))
    has_no = bool(re.search(r"\bno\b", value))
    if has_yes and not has_no:
        return True
    if has_no and not has_yes:
        return False
    raise ValueError(
        f"judge response is not unambiguous yes/no: {text[:200]}"
    )


def judge_answer(
    llm,
    task: str,
    question: str,
    answer: str,
    response: str,
    *,
    abstention: bool = False,
    attempts: int = 3,
) -> bool:
    prompt = build_answer_check_prompt(
        task,
        question,
        answer,
        response,
        abstention=abstention,
    )
    return chat_with_repair(
        llm,
        "You are an answer evaluation assistant.",
        prompt,
        repair_prompt=LONGMEMEVAL_REPAIR_PROMPT,
        parse=parse_yes_no,
        attempts=attempts,
        # Yes/no verdict is a plain word, not JSON: keep response_format off
        # (thinking stays disabled and max_tokens stays uncapped via defaults).
        response_format=False,
    )
