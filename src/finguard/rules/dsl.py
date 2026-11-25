"""DSL 表达式：词法分析 + 递归下降解析。

设计约束（安全第一）：
    规则 DSL 是**外部内容**，绝不能用 eval / exec 执行。
    本模块只做「解析成一棵不可变的语法树」，求值在 engine.py 里用纯比较完成。

支持的语法（唯一形态）：

    IF <operand> <op> <operand> THEN hit('<label>')

    operand := fact.<字段名> | 整数 | 浮点数 | '字符串' | "字符串"
    op      := < | <= | > | >= | == | !=

严格性（刻意的）：
    IF / THEN 必须全大写，hit 必须全小写 —— 拼写错误要在**解析期**暴露，
    而不是等到某天规则跑到才静默走偏；
    解析器不吃任何多余字符，表达式尾部有多余内容直接报错；
    字段名只允许「字母 / 数字 / 下划线」，且不能以数字开头。

依据：PRD 6.1 FR-RAG-01（判定走确定性引擎）、FR-AUDIT-03（严重度取自规则库）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Union

__all__ = [
    "DslError",
    "DslParseError",
    "Comparison",
    "FieldRef",
    "Literal",
    "Operand",
    "RuleExpression",
    "parse_expression",
]


class DslError(ValueError):
    """DSL 相关错误基类。"""


class DslParseError(DslError):
    """表达式无法解析。位置信息用于回写规则维护界面。"""

    def __init__(self, message: str, position: Optional[int] = None, source: str = "") -> None:
        self.position = position
        self.source = source
        if position is not None and source:
            message = "%s（第 %d 个字符处）" % (message, position)
        super().__init__(message)


# ---------------------------------------------------------------- 词法
_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>-?[0-9]+(?:\.[0-9]+)?)
  | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|==|!=|<|>)
  | (?P<punct>[.()])
    """,
    re.VERBOSE,
)

_KEYWORDS = {"IF", "THEN", "hit", "fact"}


@dataclass(frozen=True)
class Token:
    kind: str      # number / string / ident / op / punct
    value: Any
    position: int


def tokenize(text: str) -> List[Token]:
    """把表达式切成记号；遇到无法识别的字符立即报错。"""
    if not isinstance(text, str):
        raise DslParseError("表达式必须是字符串，实际是 %r" % type(text).__name__)
    tokens: List[Token] = []
    pos = 0
    length = len(text)
    while pos < length:
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise DslParseError("无法识别的字符 %r" % text[pos], pos, text)
        kind = m.lastgroup
        value = m.group()
        start = m.start()
        pos = m.end()
        if kind == "ws":
            continue
        if kind == "number":
            tokens.append(Token("number", _to_number(value), start))
        elif kind == "string":
            tokens.append(Token("string", _unquote(value), start))
        elif kind == "ident":
            tokens.append(Token("ident", value, start))
        elif kind == "op":
            tokens.append(Token("op", value, start))
        else:
            tokens.append(Token("punct", value, start))
    return tokens


def _to_number(raw: str) -> Union[int, float]:
    if "." in raw:
        return float(raw)
    return int(raw)


def _unquote(raw: str) -> str:
    body = raw[1:-1]
    return body.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")


# ---------------------------------------------------------------- 语法树
@dataclass(frozen=True)
class Literal:
    """常量操作数。"""

    value: Any


@dataclass(frozen=True)
class FieldRef:
    """事实字段引用（fact.<name>）。"""

    name: str


Operand = Union[Literal, FieldRef]


@dataclass(frozen=True)
class Comparison:
    """lhs <op> rhs。"""

    lhs: Operand
    op: str
    rhs: Operand

    def field_names(self) -> Tuple[str, ...]:
        names = []
        for side in (self.lhs, self.rhs):
            if isinstance(side, FieldRef):
                names.append(side.name)
        return tuple(names)


@dataclass(frozen=True)
class RuleExpression:
    """一条完整表达式：条件成立时给出 label。"""

    label: str
    comparison: Comparison
    source: str = ""

    def field_names(self) -> Tuple[str, ...]:
        return self.comparison.field_names()

    def describe(self) -> str:
        return "%s %s %s" % (
            _operand_text(self.comparison.lhs),
            self.comparison.op,
            _operand_text(self.comparison.rhs),
        )


def _operand_text(op: Operand) -> str:
    if isinstance(op, FieldRef):
        return "fact." + op.name
    if isinstance(op, Literal):
        if isinstance(op.value, str):
            return "'%s'" % op.value
        return str(op.value)
    return "?"


# ---------------------------------------------------------------- 解析
_COMPARISON_OPS = ("<", "<=", ">", ">=", "==", "!=")


class _Parser:
    def __init__(self, tokens: List[Token], source: str) -> None:
        self._tokens = tokens
        self._source = source
        self._i = 0

    def _peek(self) -> Optional[Token]:
        return self._tokens[self._i] if self._i < len(self._tokens) else None

    def _next(self) -> Token:
        tok = self._peek()
        if tok is None:
            raise DslParseError("表达式意外结束", None, self._source)
        self._i += 1
        return tok

    def _expect_ident(self, word: str) -> Token:
        tok = self._next()
        if tok.kind != "ident" or tok.value != word:
            raise DslParseError(
                "期望 %r，实际是 %r" % (word, tok.value), tok.position, self._source
            )
        return tok

    def parse(self) -> RuleExpression:
        self._expect_ident("IF")
        lhs = self._parse_operand()
        op_tok = self._next()
        if op_tok.kind != "op" or op_tok.value not in _COMPARISON_OPS:
            raise DslParseError(
                "期望比较运算符 %s，实际是 %r" % ("/".join(_COMPARISON_OPS), op_tok.value),
                op_tok.position,
                self._source,
            )
        rhs = self._parse_operand()
        self._expect_ident("THEN")
        self._expect_ident("hit")
        paren = self._next()
        if paren.kind != "punct" or paren.value != "(":
            raise DslParseError("期望 '('", paren.position, self._source)
        label_tok = self._next()
        if label_tok.kind != "string":
            raise DslParseError("hit 的参数必须是字符串", label_tok.position, self._source)
        if not label_tok.value.strip():
            raise DslParseError("hit 的 label 不能为空或纯空白", label_tok.position, self._source)
        close = self._next()
        if close.kind != "punct" or close.value != ")":
            raise DslParseError("期望 ')'", close.position, self._source)
        if self._peek() is not None:
            extra = self._peek()
            raise DslParseError("表达式尾部有多余内容", extra.position, self._source)
        return RuleExpression(
            label=label_tok.value,
            comparison=Comparison(lhs=lhs, op=op_tok.value, rhs=rhs),
            source=self._source,
        )

    def _parse_operand(self) -> Operand:
        tok = self._next()
        if tok.kind == "number":
            return Literal(tok.value)
        if tok.kind == "string":
            return Literal(tok.value)
        if tok.kind == "ident" and tok.value == "fact":
            dot = self._next()
            if dot.kind != "punct" or dot.value != ".":
                raise DslParseError("'fact' 后期望 '.'", dot.position, self._source)
            name = self._next()
            if name.kind != "ident" or name.value in _KEYWORDS:
                raise DslParseError("字段名非法：%r" % (name.value,), name.position, self._source)
            return FieldRef(name.value)
        raise DslParseError("无法解析的操作数：%r" % (tok.value,), tok.position, self._source)


def parse_expression(text: str) -> RuleExpression:
    """解析一条 DSL 表达式。

    失败一律抛 DslParseError（带位置与原文），**不吞异常**——
    调用方（引擎）负责把它降级成「未判定」。
    """
    if text is None or not str(text).strip():
        raise DslParseError("表达式为空", None, str(text))
    source = str(text)
    tokens = tokenize(source)
    if not tokens:
        raise DslParseError("表达式为空", None, source)
    return _Parser(tokens, source).parse()
