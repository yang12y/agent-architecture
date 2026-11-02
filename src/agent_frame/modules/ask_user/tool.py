"""`ask_user`：模型用来向用户提问的工具。

一次调用做三件事：**把参数校验成问题**、**挂起等回答**、**把回答原样交给模型**。
挂起发生在提问端口里（见 QuestionGate）—— 工具自己不懂怎么等人。

回答作为一条普通工具结果回到上下文，所以它**进日志**：重连后历史里还在。
"""

from __future__ import annotations

from agent_frame.contracts import (
    JsonValue,
    Permission,
    Question,
    QuestionAnswer,
    QuestionOption,
    QuestionProvider,
    QuestionRequest,
    SideEffect,
    ToolArgumentError,
    ToolContext,
    ToolDeclaration,
    ToolRunner,
)

# 等一个人回答最多等多久（秒）。**人不会在默认的 60 秒内必答** —— 声明在这儿，
# 执行器按它放宽超时。超时之后调用失败，模型自己决定怎么办。
#
# 举例：300.0
ANSWER_WAIT_SECONDS = 300.0

# 工具声明。
#
# 副作用记 non_idempotent：问两遍就是真的问了用户两遍，失败不该自动重试 ——
# 重试会让界面上多出一张一模一样的卡片。
#
# 举例：ASK_USER_TOOL.name == "ask_user"
ASK_USER_TOOL = ToolDeclaration(
    name="ask_user",
    description=(
        "向用户提问：需要他确认、需要在几个方案里挑一个，或者缺了只有他知道的信息时用它。"
        "一次把所有问题一起问完；每个问题给一个稳定的 id，回答按 id 带回来。"
        "能自己查到、能自己判断的不要问，问之前先把能查的查完。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "要问的问题，按问的顺序，一次问完。",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "这次提问内的稳定标识，回答里原样带回。",
                        },
                        "question": {
                            "type": "string",
                            "description": "问题正文，一句话说清要他选什么或确认什么。",
                        },
                        "header": {
                            "type": "string",
                            "description": "卡片的短标题，比如「确认写入」。",
                        },
                        "options": {
                            "type": "array",
                            "description": (
                                "可选项。**推荐的那个放第一个**，并在 label 后面加「（推荐）」。"
                                "不给就只能自由作答。"
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "description": "按钮上的文字，回答里按它配对。",
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "一句话说明选它会怎样。",
                                    },
                                },
                                "required": ["label"],
                            },
                        },
                        "multi_select": {
                            "type": "boolean",
                            "description": "能不能多选，默认单选。",
                        },
                    },
                    "required": ["id", "question"],
                },
            }
        },
        "required": ["questions"],
    },
    output_contract=(
        "每个问题一条答案：selected 是选中的选项，custom 是用户自己打的字（没打就没有这个键）。"
    ),
    side_effect=SideEffect.NON_IDEMPOTENT,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
    timeout=ANSWER_WAIT_SECONDS,
)


def make_runner(questions: QuestionProvider) -> ToolRunner:
    """按一个提问端口造出工具实现。

    提问端口是进程级的，所以实现也只能在装配期生成 —— 工具签名里没有它的位置。

    举例：catalog.register(ASK_USER_TOOL, make_runner(container.questions))
    """

    async def run(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
        """问一遍用户，把他的回答摊成 JSON。

        举例：await run(ctx, {"questions": [{"id": "go", "question": "继续吗？"}]})
              -> {"answers": [{"id": "go", "selected": [], "custom": "继续"}]}
        出错：参数不合法时抛 ToolArgumentError；没人能答时由提问端口抛 NoAnswererError。
        """
        request = QuestionRequest(
            # 一次工具调用只问一次，所以调用 id 就是这次提问的 id —— 不用再要一个生成器。
            request_id=f"q_{ctx.call_id}",
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
            tool_call_id=ctx.call_id,
            tool_name=ASK_USER_TOOL.name,
            questions=parse_questions(arguments),
        )
        result = await questions.request(request)
        return {"answers": encode_answers(result.answers)}

    return run


def parse_questions(arguments: JsonValue) -> tuple[Question, ...]:
    """把工具参数校验成一组问题。

    规则与工具描述里说的一致：至少一个、id 不重复、正文非空、同一题里选项不重名。

    举例：parse_questions({"questions": [{"id": "go", "question": "继续吗？"}]})
          -> (Question(id="go", question="继续吗？", ...),)
    出错：任何一条不合法时抛 ToolArgumentError。
    """
    raw = arguments.get("questions") if isinstance(arguments, dict) else None
    if not isinstance(raw, list):
        raise ToolArgumentError("questions 必须是一个数组")
    if not raw:
        raise ToolArgumentError("至少要问一个问题")

    questions: list[Question] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ToolArgumentError(f"questions 里每一条都要是对象，收到 {type(item).__name__}")
        question_id = _text(item.get("id"), "id")
        if question_id in seen:
            raise ToolArgumentError(f"问题 id 重复：{question_id}")
        seen.add(question_id)
        questions.append(
            Question(
                id=question_id,
                question=_text(item.get("question"), "question"),
                header=_optional_text(item.get("header"), "header"),
                options=_options(item.get("options"), question_id),
                multi_select=item.get("multi_select") is True,
            )
        )
    return tuple(questions)


def encode_answers(answers: tuple[QuestionAnswer, ...]) -> list[JsonValue]:
    """把回答摊成给模型看的 JSON。**custom 没填就不带这个键**，省得模型以为用户说了空话。

    举例：encode_answers((QuestionAnswer(id="go", selected=("继续",), custom=None),))
          -> [{"id": "go", "selected": ["继续"]}]
    """
    encoded: list[JsonValue] = []
    for answer in answers:
        item: dict[str, JsonValue] = {"id": answer.id, "selected": list(answer.selected)}
        if answer.custom:
            item["custom"] = answer.custom
        encoded.append(item)
    return encoded


def _text(raw: JsonValue, field: str) -> str:
    """取一个必填的文本字段：trim 之后不能是空串。

    举例：_text(" go ", "id") -> "go"
    出错：不是字符串或空串时抛 ToolArgumentError。
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ToolArgumentError(f"{field} 必须是非空字符串")
    return raw.strip()


def _optional_text(raw: JsonValue, field: str) -> str | None:
    """取一个选填的文本字段：没给就是 None，给了就必须非空。

    举例：_optional_text(None, "header") -> None
    出错：给了但不是字符串或空串时抛 ToolArgumentError。
    """
    if raw is None:
        return None
    return _text(raw, field)


def _options(raw: JsonValue, question_id: str) -> tuple[QuestionOption, ...]:
    """取一个问题里的可选项：**同一题里 label 不能重名** —— 回答按它配对。

    举例：_options([{"label": "继续"}], "go") -> (QuestionOption(label="继续", description=None),)
    出错：形状不对或重名时抛 ToolArgumentError。
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ToolArgumentError(f"问题 {question_id} 的 options 必须是数组")

    options: list[QuestionOption] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ToolArgumentError(f"问题 {question_id} 的 options 里每一条都要是对象")
        label = _text(item.get("label"), "label")
        if label in seen:
            raise ToolArgumentError(f"问题 {question_id} 的选项重名：{label}")
        seen.add(label)
        options.append(
            QuestionOption(
                label=label, description=_optional_text(item.get("description"), "description")
            )
        )
    return tuple(options)
