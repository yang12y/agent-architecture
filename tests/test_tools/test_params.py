"""参数 DSL 的测试：生成的 schema 只有检查链认的那几样。"""

from __future__ import annotations

from agent_frame.tools.params import choice, integer, list_of, obj, text


def test_obj_collects_required_from_fields() -> None:
    """必填是字段上的标记，obj 负责汇总到父级。"""
    schema = obj({"name": text("名称", required=True), "code": text("编码")})

    assert schema["required"] == ["name"]
    assert "x-required" not in str(schema)


def test_obj_without_required_has_no_required_key() -> None:
    """一个必填都没有时，不写 required —— 空列表会让检查链白跑一遍。"""
    assert "required" not in obj({"name": text("名称")})


def test_choice_keeps_enum_and_type() -> None:
    """枚举参数：检查链会真的拦非法值。"""
    spec = choice(["0", "1"], "状态")

    assert spec["type"] == "string"
    assert spec["enum"] == ["0", "1"]


def test_integer_carries_minimum_for_the_model() -> None:
    """minimum 只是给模型看的约束，检查链不校验它。"""
    assert integer("条数", minimum=1)["minimum"] == 1


def test_list_of_keeps_item_schema() -> None:
    """数组元素形状原样保留。"""
    assert list_of({"type": "string"}, "guid 列表")["items"] == {"type": "string"}
