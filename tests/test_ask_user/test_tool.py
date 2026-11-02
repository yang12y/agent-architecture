"""`ask_user` 的参数校验与返回值。"""

from __future__ import annotations

import pytest

from agent_frame.contracts import (
    CancelToken,
    Question,
    QuestionAnswer,
    QuestionOption,
    QuestionRequest,
    QuestionResult,
    ToolArgumentError,
    ToolContext,
)
from agent_frame.infra.observability import RecordingLogger
from agent_frame.modules.ask_user import (
    ANSWER_WAIT_SECONDS,
    ASK_USER_TOOL,
    encode_answers,
    make_runner,
    parse_questions,
)


class _FakeQuestions:
    """记下发出的提问，直接回一份答案的假端口。

    举例：port = _FakeQuestions(()); await make_runner(port)(ctx, {...})
    """

    def __init__(self, answers: tuple[QuestionAnswer, ...]) -> None:
        # 回给工具的那份答案。
        #
        # 举例：(QuestionAnswer(id="go", selected=("继续",), custom=None),)
        self._answers = answers

        # 工具发出来的那次提问，供断言用。
        #
        # 举例：QuestionRequest(request_id="q_call_1", ...)
        self.seen: QuestionRequest | None = None

    async def request(self, request: QuestionRequest) -> QuestionResult:
        """照单收下，立刻给答案。

        举例：await port.request(QuestionRequest(...))
        """
        self.seen = request
        return QuestionResult(request_id=request.request_id, answers=self._answers)

    async def pending(self, session_id: str) -> list[QuestionRequest]:
        """假端口上没有待答的。

        举例：await port.pending("sess_1") -> []
        """
        del session_id
        return []

    async def respond(self, session_id: str, result: QuestionResult) -> bool:
        """假端口不接受外部回答。

        举例：await port.respond("sess_1", result) -> False
        """
        del session_id, result
        return False


def _context() -> ToolContext:
    """一次工具执行的现场。

    举例：_context().call_id -> "call_1"
    """
    return ToolContext(
        workspace=".",
        call_id="call_1",
        signal=CancelToken(),
        logger=RecordingLogger(),
        session_id="sess_1",
        trace_id="trace_1",
    )


def test_a_clean_question_list_is_parsed() -> None:
    """trim 之后存进去；没给的可选项是空元组，不是 None。"""
    questions = parse_questions(
        {"questions": [{"id": " go ", "question": " 继续吗？ ", "options": [{"label": "继续"}]}]}
    )

    assert questions == (
        Question(
            id="go",
            question="继续吗？",
            header=None,
            options=(QuestionOption(label="继续", description=None),),
            multi_select=False,
        ),
    )


def test_a_missing_questions_key_is_an_error() -> None:
    """没给 questions 不合法。"""
    with pytest.raises(ToolArgumentError):
        parse_questions({})


def test_an_empty_question_list_is_an_error() -> None:
    """一个问题都没有的调用没意义 —— 它会让界面上出现一张空卡。"""
    with pytest.raises(ToolArgumentError):
        parse_questions({"questions": []})


def test_duplicate_question_ids_are_rejected() -> None:
    """id 是回执配对的键，重复就分不清答的是哪一问。"""
    with pytest.raises(ToolArgumentError):
        parse_questions(
            {
                "questions": [
                    {"id": "go", "question": "继续吗？"},
                    {"id": "go", "question": "真的？"},
                ]
            }
        )


def test_a_blank_question_is_rejected() -> None:
    """空白正文拒绝：界面上会是一张没有问题的卡片。"""
    with pytest.raises(ToolArgumentError):
        parse_questions({"questions": [{"id": "go", "question": "   "}]})


def test_duplicate_option_labels_are_rejected() -> None:
    """选项按 label 配对，重名就配错。"""
    with pytest.raises(ToolArgumentError):
        parse_questions(
            {
                "questions": [
                    {
                        "id": "go",
                        "question": "继续吗？",
                        "options": [{"label": "继续"}, {"label": "继续"}],
                    }
                ]
            }
        )


def test_multi_select_is_a_boolean_not_truthy_junk() -> None:
    """只有明确的 true 才算多选 —— 别把字符串 "yes" 当成真。"""
    assert parse_questions(
        {"questions": [{"id": "go", "question": "继续吗？", "multi_select": True}]}
    )[0].multi_select
    assert not parse_questions(
        {"questions": [{"id": "go", "question": "继续吗？", "multi_select": "yes"}]}
    )[0].multi_select


def test_answers_drop_an_empty_custom_text() -> None:
    """用户没自己打字就不带 custom 这个键 —— 省得模型以为他说了句空话。"""
    encoded = encode_answers(
        (QuestionAnswer(id="go", selected=("继续",), custom=None),),
    )

    assert encoded == [{"id": "go", "selected": ["继续"]}]


async def test_the_runner_round_trips_through_the_question_port() -> None:
    """工具把问题交给端口，把答案摊成 JSON 交回模型。"""
    port = _FakeQuestions((QuestionAnswer(id="go", selected=("继续",), custom="顺便看下日志"),))
    runner = make_runner(port)

    result = await runner(_context(), {"questions": [{"id": "go", "question": "继续吗？"}]})

    assert result == {"answers": [{"id": "go", "selected": ["继续"], "custom": "顺便看下日志"}]}
    assert port.seen is not None
    assert port.seen.request_id == "q_call_1"
    assert port.seen.session_id == "sess_1"
    assert port.seen.tool_call_id == "call_1"


def test_the_declaration_waits_much_longer_than_the_default_timeout() -> None:
    """等人工的工具必须放宽超时，否则人还没想完这次调用就失败了。"""
    assert ASK_USER_TOOL.timeout == ANSWER_WAIT_SECONDS
    assert ANSWER_WAIT_SECONDS > 60


def test_the_declaration_is_never_retried() -> None:
    """问两遍就是真的问了两遍 —— 重试会在界面上多出一张一样的卡。"""
    assert ASK_USER_TOOL.accepts_idempotency_key is False
