"""待回答提问与回答。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agent_frame.composition import Container
from agent_frame.contracts import Question, QuestionAnswer, QuestionRequest, QuestionResult

from ..deps import get_container
from ..errors import bad_answers, question_not_found, session_not_found
from ..schemas import (
    PendingQuestionsOut,
    QuestionAcceptedOut,
    QuestionAnswersIn,
    QuestionRequestOut,
)

router = APIRouter(tags=["questions"])


@router.get("/questions", response_model=PendingQuestionsOut)
async def list_questions(
    user_id: str,
    session_id: str,
    container: Container = Depends(get_container),
) -> PendingQuestionsOut:
    """这个会话里还没答的提问。

    **断线期间到达的提问只能从这里补** —— 它没有对应的 Session 条目，事件补不回来。

    举例：GET /questions?user_id=u1&session_id=sess_x -> {"questions": [...]}
    """
    live = await container.sessions.get_or_load(session_id, user_id)
    if live is None:
        raise session_not_found(session_id)

    pending = await container.questions.pending(session_id)
    return PendingQuestionsOut(
        questions=[QuestionRequestOut.from_request(item) for item in pending]
    )


@router.post("/questions/{request_id}", response_model=QuestionAcceptedOut)
async def answer(
    request_id: str,
    user_id: str,
    session_id: str,
    body: QuestionAnswersIn,
    container: Container = Depends(get_container),
) -> QuestionAcceptedOut:
    """交一份回答，那条挂着的工具调用立刻往下走。

    **答案对不上问题就整份拒掉**：收半份会让模型以为用户只答了一半。

    举例：POST /questions/q_1?user_id=u1&session_id=sess_x {"answers": [{"id": "go", ...}]}
    """
    live = await container.sessions.get_or_load(session_id, user_id)
    if live is None:
        raise session_not_found(session_id)

    pending = await container.questions.pending(session_id)
    request = next((item for item in pending if item.request_id == request_id), None)
    if request is None:
        raise question_not_found(request_id)

    result = QuestionResult(request_id=request_id, answers=_answers_of(request, body))
    if not await container.questions.respond(session_id, result):
        raise question_not_found(request_id)
    return QuestionAcceptedOut(request_id=request_id, accepted=True)


def _answers_of(request: QuestionRequest, body: QuestionAnswersIn) -> tuple[QuestionAnswer, ...]:
    """把提交的答案核对成规范答案：不认识的 id、不存在的选项、单选给了两个都拒掉。

    举例：_answers_of(request, QuestionAnswersIn(answers=[...]))
    出错：对不上时抛 bad_answers（400）。
    """
    by_id = {answer.id: answer for answer in body.answers}
    if len(by_id) != len(body.answers):
        raise bad_answers("同一个问题给了两份答案")

    expected = {question.id for question in request.questions}
    if set(by_id) != expected:
        missing = sorted(expected - set(by_id))
        unknown = sorted(set(by_id) - expected)
        raise bad_answers(f"答案与问题对不上：缺 {missing}，多 {unknown}")

    answers: list[QuestionAnswer] = []
    for question in request.questions:
        given = by_id[question.id]
        selected = _selected_of(question, given.selected)
        custom = (given.custom or "").strip() or None
        if not selected and custom is None:
            raise bad_answers(f"问题 {question.id} 既没选选项也没填字")
        answers.append(QuestionAnswer(id=question.id, selected=selected, custom=custom))
    return tuple(answers)


def _selected_of(question: Question, selected: list[str]) -> tuple[str, ...]:
    """核对待选项：都在、单选只给一个，然后**按选项声明的顺序排**。

    举例：_selected_of(question, ["继续"]) -> ("继续",)
    出错：选项不存在、或者单选给了多个时抛 bad_answers。
    """
    labels = [option.label for option in question.options]
    unknown = [label for label in selected if label not in labels]
    if unknown:
        raise bad_answers(f"问题 {question.id} 里没有选项：{unknown}")
    if not question.multi_select and len(selected) > 1:
        raise bad_answers(f"问题 {question.id} 是单选，收到了 {len(selected)} 个选项")
    return tuple(label for label in labels if label in set(selected))
