# mypy: allow-untyped-defs
from __future__ import annotations

import argparse
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
import os
from typing import Any
from typing import cast
from typing import final
from typing import Literal
from typing import NoReturn

import _pytest._io
from _pytest.config.exceptions import UsageError
from _pytest.deprecated import check_ispytest


# 常量：用于标识文件或目录参数的名称
FILE_OR_DIR = "file_or_dir"


class NotSet:
    """特殊的标记类，用于表示参数未设置的状态。
    
    这个类提供了一个明确的标识符，用于区分参数未设置和参数设置为 None 的情况。
    在配置系统中，None 可能是一个有效的默认值，而 NotSet 表示完全没有设置。
    """
    def __repr__(self) -> str:
        """返回 NotSet 对象的字符串表示。
        
        用于调试和日志记录，显示这是一个特殊的"未设置"标记对象。
        
        :return: 固定的字符串 "<notset>"，表示这是一个未设置状态的标记对象
        :rtype: str
        """
        return "<notset>"


# 全局单例：表示未设置状态的唯一实例
NOT_SET = NotSet()


@final
class Parser:
    """pytest 的命令行参数和配置文件解析器。
    
    这是 pytest 框架的核心解析器，负责：
    1. 解析命令行参数
    2. 处理 ini 配置文件选项
    3. 管理选项分组
    4. 提供统一的配置接口

    :ivar extra_info: 在处理命令行参数出错时显示的通用参数字典
    """

    prog: str | None = None  # 程序名称，用于帮助信息

    def __init__(
        self,
        usage: str | None = None,  # 使用说明字符串
        processopt: Callable[[Argument], None] | None = None,  # 选项处理回调函数
        *,
        _ispytest: bool = False,  # 内部使用标记，确保只有 pytest 内部可以创建实例
    ) -> None:
        check_ispytest(_ispytest)  # 验证调用方是否为 pytest 内部代码
        
        # 匿名选项组，用于存放未分组的选项
        self._anonymous = OptionGroup("Custom options", parser=self, _ispytest=True)
        # 选项组列表，用于组织不同类别的命令行选项
        self._groups: list[OptionGroup] = []
        # 选项处理回调函数
        self._processopt = processopt
        # 使用说明
        self._usage = usage
        # ini 配置选项字典：{名称: (帮助信息, 类型, 默认值)}
        self._inidict: dict[str, tuple[str, str, Any]] = {}
        # ini 配置别名映射：{别名: 规范名称}
        self._ini_aliases: dict[str, str] = {}
        # 额外信息字典，用于错误显示
        self.extra_info: dict[str, Any] = {}

    def processoption(self, option: Argument) -> None:
        """处理选项的回调方法。
        
        如果设置了选项处理回调函数，则调用它来处理给定的选项。
        这允许在选项添加时执行自定义逻辑。
        
        :param option: 要处理的参数选项对象
        """
        if self._processopt:
            if option.dest:
                self._processopt(option)

    def getgroup(
        self, name: str, description: str = "", after: str | None = None
    ) -> OptionGroup:
        """获取或创建命名的选项组。

        :param name: 选项组的名称
        :param description: 用于 --help 输出的详细描述
        :param after: 另一个组的名称，用于排序 --help 输出
        :returns: 选项组对象

        返回的组对象有一个 ``addoption`` 方法，签名与
        :func:`parser.addoption <pytest.Parser.addoption>` 相同，
        但在 ``pytest --help`` 输出中会显示在相应的组中。
        
        这个方法允许将相关的命令行选项组织在一起，使帮助信息更加清晰。
        """
        # 查找是否已存在同名组
        for group in self._groups:
            if group.name == name:
                return group
        
        # 创建新的选项组
        group = OptionGroup(name, description, parser=self, _ispytest=True)
        
        # 确定插入位置（在指定组之后）
        i = 0
        for i, grp in enumerate(self._groups):
            if grp.name == after:
                break
        self._groups.insert(i + 1, group)
        return group

    def addoption(self, *opts: str, **attrs: Any) -> None:
        """注册命令行选项。

        :param opts: 选项名称，可以是短选项或长选项
        :param attrs: 与 argparse 库的 :meth:`add_argument()
            <argparse.ArgumentParser.add_argument>` 函数接受的相同属性

        命令行解析后，选项可通过 ``config.option.NAME`` 在 pytest 配置对象上访问，
        其中 ``NAME`` 通常通过传递 ``dest`` 属性设置，例如
        ``addoption("--long", dest="NAME", ...)``。
        
        这是添加 pytest 命令行选项的主要方法。选项会被添加到匿名组中。
        """
        self._anonymous.addoption(*opts, **attrs)

    def parse(
        self,
        args: Sequence[str | os.PathLike[str]],
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        """解析命令行参数。
        
        :param args: 要解析的参数序列
        :param namespace: 可选的命名空间对象，用于存储解析结果
        :returns: 包含解析结果的 argparse.Namespace 对象
        
        这个方法创建内部解析器，启用自动补全功能，然后解析参数。
        """
        from _pytest._argcomplete import try_argcomplete

        # 创建选项解析器
        self.optparser = self._getparser()
        # 尝试启用 argcomplete 自动补全
        try_argcomplete(self.optparser)
        # 将路径对象转换为字符串
        strargs = [os.fspath(x) for x in args]
        # 解析参数
        return self.optparser.parse_args(strargs, namespace=namespace)

    def _getparser(self) -> MyOptionParser:
        """创建并配置内部的 argparse 解析器。
        
        :returns: 配置好的 MyOptionParser 实例
        
        这个方法：
        1. 创建自定义的选项解析器
        2. 添加所有选项组到解析器
        3. 添加文件/目录位置参数
        4. 配置自动补全功能
        """
        from _pytest._argcomplete import filescompleter

        # 创建自定义解析器
        optparser = MyOptionParser(self, self.extra_info, prog=self.prog)
        # 合并所有选项组（包括匿名组）
        groups = [*self._groups, self._anonymous]
        
        # 为每个选项组添加参数
        for group in groups:
            if group.options:
                desc = group.description or group.name
                arggroup = optparser.add_argument_group(desc)
                for option in group.options:
                    n = option.names()  # 获取选项名称列表
                    a = option.attrs()  # 获取选项属性
                    arggroup.add_argument(*n, **a)
        
        # 添加文件或目录位置参数
        file_or_dir_arg = optparser.add_argument(FILE_OR_DIR, nargs="*")
        # 为目录参数启用 bash 风格的自动补全（追加 '/'）
        # Type ignored because typeshed doesn't know about argcomplete.
        file_or_dir_arg.completer = filescompleter  # type: ignore
        return optparser

    def parse_setoption(
        self,
        args: Sequence[str | os.PathLike[str]],
        option: argparse.Namespace,
        namespace: argparse.Namespace | None = None,
    ) -> list[str]:
        """解析参数并将结果设置到指定的命名空间对象中。
        
        :param args: 要解析的参数序列
        :param option: 目标命名空间对象，解析结果将设置到此对象
        :param namespace: 可选的命名空间对象
        :returns: 文件或目录参数列表
        
        这个方法解析参数并将所有解析结果复制到提供的 option 对象中，
        返回文件/目录参数列表。
        """
        parsedoption = self.parse(args, namespace=namespace)
        # 将解析结果复制到目标对象
        for name, value in parsedoption.__dict__.items():
            setattr(option, name, value)
        return cast(list[str], getattr(parsedoption, FILE_OR_DIR))

    def parse_known_args(
        self,
        args: Sequence[str | os.PathLike[str]],
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        """解析已知的参数，忽略未知参数。

        :param args: 要解析的参数序列
        :param namespace: 可选的命名空间对象
        :returns: 包含已知参数的 argparse 命名空间对象
        
        这是 parse_known_and_unknown_args 的简化版本，只返回已知参数。
        """
        return self.parse_known_and_unknown_args(args, namespace=namespace)[0]

    def parse_known_and_unknown_args(
        self,
        args: Sequence[str | os.PathLike[str]],
        namespace: argparse.Namespace | None = None,
    ) -> tuple[argparse.Namespace, list[str]]:
        """解析已知参数并返回未知参数列表。

        :param args: 要解析的参数序列
        :param namespace: 可选的命名空间对象
        :returns: 包含已知参数的命名空间对象和未知参数列表的元组
        
        这个方法允许处理部分已知的参数，同时保留未知参数供后续处理。
        """
        optparser = self._getparser()
        strargs = [os.fspath(x) for x in args]
        return optparser.parse_known_args(strargs, namespace=namespace)

    def addini(
        self,
        name: str,
        help: str,
        type: Literal[
            "string", "paths", "pathlist", "args", "linelist", "bool", "int", "float"
        ]
        | None = None,
        default: Any = NOT_SET,
        *,
        aliases: Sequence[str] = (),
    ) -> None:
        """注册 ini 配置文件选项。

        :param name: ini 变量的名称
        :param help: 选项的帮助信息
        :param type: 变量的类型，可以是：
                * ``string``: 字符串
                * ``bool``: 布尔值
                * ``args``: 字符串列表，按 shell 方式分隔
                * ``linelist``: 字符串列表，按换行符分隔
                * ``paths``: pathlib.Path 对象列表，按 shell 方式分隔
                * ``pathlist``: py.path 对象列表，按 shell 方式分隔
                * ``int``: 整数
                * ``float``: 浮点数

                .. versionadded:: 8.4
                    ``float`` 和 ``int`` 类型。

            对于 ``paths`` 和 ``pathlist`` 类型，它们被认为是相对于 ini 文件的。
            如果在没有定义 ini 文件的情况下执行，它们将被认为是相对于当前工作目录的
            （例如使用 ``--override-ini`` 时）。

            .. versionadded:: 7.0
                ``paths`` 变量类型。

            .. versionadded:: 8.1
                在没有 ini 文件时使用当前工作目录解析 ``paths`` 和 ``pathlist``。

            如果为 ``None`` 或未传递，默认为 ``string``。
        :param default: 如果不存在 ini 文件选项但被查询时的默认值
        :param aliases: 可以引用此选项的其他名称。别名解析为规范名称。

            .. versionadded:: 9.0
                ``aliases`` 参数。

        ini 变量的值可以通过调用
        :py:func:`config.getini(name) <pytest.Config.getini>` 来获取。
        
        这个方法允许插件和 pytest 核心注册配置文件选项，这些选项可以在
        pytest.ini、pyproject.toml、setup.cfg 或 tox.ini 文件中设置。
        """
        # 验证类型参数的有效性
        assert type in (
            None,
            "string",
            "paths",
            "pathlist",
            "args",
            "linelist",
            "bool",
            "int",
            "float",
        )
        # 默认类型为 string
        if type is None:
            type = "string"
        # 如果没有提供默认值，根据类型获取合适的默认值
        if default is NOT_SET:
            default = get_ini_default_for_type(type)

        # 将配置选项存储到内部字典中
        self._inidict[name] = (help, type, default)

        # 处理别名
        for alias in aliases:
            # 检查别名是否与现有选项冲突
            if alias in self._inidict:
                raise ValueError(f"alias {alias!r} conflicts with existing ini option")
            # 检查别名是否已经是其他选项的别名
            if (already := self._ini_aliases.get(alias)) is not None:
                raise ValueError(f"{alias!r} is already an alias of {already!r}")
            # 存储别名映射
            self._ini_aliases[alias] = name


def get_ini_default_for_type(
    type: Literal[
        "string", "paths", "pathlist", "args", "linelist", "bool", "int", "float"
    ],
) -> Any:
    """根据 ini 选项类型获取合适的默认值。
    
    这个函数被 addini 使用，当没有提供默认值时，为给定的 ini 选项类型
    返回一个合理的默认值。
    
    :param type: ini 选项的类型
    :returns: 对应类型的默认值
    
    默认值规则：
    - 列表类型（paths, pathlist, args, linelist）：空列表 []
    - 布尔类型：False
    - 整数类型：0
    - 浮点类型：0.0
    - 字符串类型：空字符串 ""
    """
    if type in ("paths", "pathlist", "args", "linelist"):
        return []  # 列表类型返回空列表
    elif type == "bool":
        return False  # 布尔类型返回 False
    elif type == "int":
        return 0  # 整数类型返回 0
    elif type == "float":
        return 0.0  # 浮点类型返回 0.0
    else:
        return ""  # 字符串类型返回空字符串


class ArgumentError(Exception):
    """当 Argument 实例使用无效或不一致的参数创建时抛出的异常。
    
    这个异常类用于处理命令行选项定义时的错误，比如选项格式不正确、
    缺少必要的参数等。
    """

    def __init__(self, msg: str, option: Argument | str) -> None:
        """初始化参数错误异常。
        
        :param msg: 错误消息
        :param option: 相关的选项对象或选项标识符
        """
        self.msg = msg  # 错误消息
        self.option_id = str(option)  # 选项标识符

    def __str__(self) -> str:
        """返回格式化的错误消息字符串。
        
        :returns: 包含选项标识符和错误消息的字符串
        """
        if self.option_id:
            return f"option {self.option_id}: {self.msg}"
        else:
            return self.msg


class Argument:
    """模拟 optparse.Option 必要行为的类。
    
    这个类是 pytest 从 optparse 迁移到 argparse 时的兼容层，
    提供了与旧版 optparse.Option 类似的接口。
    
    当前是最小化实现，忽略了一些高级特性如 choices 和整数前缀。
    
    参考：https://docs.python.org/3/library/optparse.html#optparse-standard-option-types
    """

    def __init__(self, *names: str, **attrs: Any) -> None:
        """初始化参数选项对象。
        
        :param names: 选项名称列表（如 '-v', '--verbose'）
        :param attrs: 选项属性字典（如 type, default, help 等）
        
        这个方法：
        1. 存储选项属性
        2. 解析短选项和长选项
        3. 确定目标属性名（dest）
        """
        # 存储选项属性，供 add_argument 使用
        self._attrs = attrs
        # 短选项列表（如 '-v', '-h'）
        self._short_opts: list[str] = []
        # 长选项列表（如 '--verbose', '--help'）
        self._long_opts: list[str] = []
        
        # 尝试获取类型属性
        try:
            self.type = attrs["type"]
        except KeyError:
            pass
        
        # 尝试获取默认值属性
        # 属性存在性在 Config._processopt 中测试
        try:
            self.default = attrs["default"]
        except KeyError:
            pass
        
        # 设置选项字符串
        self._set_opt_strings(names)
        
        # 确定目标属性名（dest）
        dest: str | None = attrs.get("dest")
        if dest:
            # 如果显式指定了 dest，使用它
            self.dest = dest
        elif self._long_opts:
            # 如果有长选项，使用第一个长选项（去掉 '--'，将 '-' 替换为 '_'）
            self.dest = self._long_opts[0][2:].replace("-", "_")
        else:
            # 如果只有短选项，使用短选项（去掉 '-'）
            try:
                self.dest = self._short_opts[0][1:]
            except IndexError as e:
                # 如果既没有长选项也没有短选项，抛出错误
                self.dest = "???"  # 用于错误表示
                raise ArgumentError("need a long or short option", self) from e

    def names(self) -> list[str]:
        """返回所有选项名称的列表。
        
        :returns: 包含短选项和长选项的完整列表
        """
        return self._short_opts + self._long_opts

    def attrs(self) -> Mapping[str, Any]:
        """返回用于 argparse 的属性字典。
        
        这个方法会更新任何由 processopt 设置的属性。
        
        :returns: 包含所有选项属性的映射
        """
        # 基础属性列表
        attrs = "default dest help".split()
        attrs.append(self.dest)  # 添加目标属性名
        
        # 更新属性字典
        for attr in attrs:
            try:
                self._attrs[attr] = getattr(self, attr)
            except AttributeError:
                pass
        return self._attrs

    def _set_opt_strings(self, opts: Sequence[str]) -> None:
        """设置选项字符串，直接从 optparse 移植的代码。
        
        虽然这个方法可能不是必需的，因为后续会传递给 argparse，
        但它保持了与 optparse 的兼容性。
        
        :param opts: 选项字符串序列
        """
        for opt in opts:
            if len(opt) < 2:
                # 选项字符串长度必须至少为 2
                raise ArgumentError(
                    f"invalid option string {opt!r}: "
                    "must be at least two characters long",
                    self,
                )
            elif len(opt) == 2:
                # 处理短选项（长度为 2）
                if not (opt[0] == "-" and opt[1] != "-"):
                    # 短选项必须是 -x 形式，x 不能是破折号
                    raise ArgumentError(
                        f"invalid short option string {opt!r}: "
                        "must be of the form -x, (x any non-dash char)",
                        self,
                    )
                self._short_opts.append(opt)
            else:
                # 处理长选项（长度大于 2）
                if not (opt[0:2] == "--" and opt[2] != "-"):
                    # 长选项必须以 -- 开头，后面不能直接跟破折号
                    raise ArgumentError(
                        f"invalid long option string {opt!r}: "
                        "must start with --, followed by non-dash",
                        self,
                    )
                self._long_opts.append(opt)

    def __repr__(self) -> str:
        """返回 Argument 对象的字符串表示。
        
        :returns: 包含关键信息的格式化字符串
        """
        args: list[str] = []
        if self._short_opts:
            args += ["_short_opts: " + repr(self._short_opts)]
        if self._long_opts:
            args += ["_long_opts: " + repr(self._long_opts)]
        args += ["dest: " + repr(self.dest)]
        if hasattr(self, "type"):
            args += ["type: " + repr(self.type)]
        if hasattr(self, "default"):
            args += ["default: " + repr(self.default)]
        return "Argument({})".format(", ".join(args))


class OptionGroup:
    """选项组类，用于在帮助信息中显示在独立部分的选项集合。
    
    选项组允许将相关的命令行选项组织在一起，使帮助信息更加清晰和有组织。
    """

    def __init__(
        self,
        name: str,  # 选项组名称
        description: str = "",  # 选项组描述
        parser: Parser | None = None,  # 关联的解析器
        *,
        _ispytest: bool = False,  # 内部使用标记
    ) -> None:
        check_ispytest(_ispytest)  # 验证调用方是否为 pytest 内部代码
        self.name = name  # 选项组名称
        self.description = description  # 选项组描述
        self.options: list[Argument] = []  # 选项列表
        self.parser = parser  # 关联的解析器

    def addoption(self, *opts: str, **attrs: Any) -> None:
        """向此组添加一个选项。

        如果指定了长选项的缩短版本，它将在帮助信息中被抑制。
        ``addoption('--twowords', '--two-words')``
        结果是帮助信息只显示 ``--two-words``，但 ``--twowords``
        被接受，并且自动目标在 ``args.twowords`` 中。

        :param opts: 选项名称，可以是短选项或长选项
        :param attrs: 与 argparse 库的 :meth:`add_argument()
            <argparse.ArgumentParser.add_argument>` 函数接受的相同属性
        """
        # 检查选项名称冲突
        conflict = set(opts).intersection(
            name for opt in self.options for name in opt.names()
        )
        if conflict:
            raise ValueError(f"option names {conflict} already added")
        
        # 创建选项实例并添加到组中
        option = Argument(*opts, **attrs)
        self._addoption_instance(option, shortupper=False)

    def _addoption(self, *opts: str, **attrs: Any) -> None:
        """内部方法：添加选项（允许大写短选项）。
        
        :param opts: 选项名称
        :param attrs: 选项属性
        """
        option = Argument(*opts, **attrs)
        self._addoption_instance(option, shortupper=True)

    def _addoption_instance(self, option: Argument, shortupper: bool = False) -> None:
        """内部方法：添加选项实例到组中。
        
        :param option: 要添加的选项实例
        :param shortupper: 是否允许大写短选项
        """
        if not shortupper:
            # 如果不允许大写短选项，检查短选项是否为小写
            for opt in option._short_opts:
                if opt[0] == "-" and opt[1].islower():
                    raise ValueError("lowercase shortoptions reserved")
        # 如果有关联的解析器，处理选项
        if self.parser:
            self.parser.processoption(option)
        # 将选项添加到组中
        self.options.append(option)


class MyOptionParser(argparse.ArgumentParser):
    """pytest 的自定义选项解析器，继承自 argparse.ArgumentParser。
    
    这个类扩展了标准 argparse 解析器，添加了 pytest 特定的功能：
    1. 自定义错误处理
    2. 特殊的帮助格式化
    3. 位置参数处理
    4. 配置文件支持
    """

    def __init__(
        self,
        parser: Parser,  # pytest 解析器实例
        extra_info: dict[str, Any] | None = None,  # 额外信息字典
        prog: str | None = None,  # 程序名称
    ) -> None:
        """初始化自定义选项解析器。
        
        :param parser: pytest 解析器实例
        :param extra_info: 额外信息字典，用于错误显示
        :param prog: 程序名称
        """
        self._parser = parser
        super().__init__(
            prog=prog,
            usage=parser._usage,  # 使用 pytest 解析器的使用说明
            add_help=False,  # 不添加默认的 -h/--help 选项
            formatter_class=DropShorterLongHelpFormatter,  # 使用自定义帮助格式化器
            allow_abbrev=False,  # 不允许缩写选项
            fromfile_prefix_chars="@",  # 支持从文件读取参数
        )
        # extra_info 是一个 (参数 -> 值) 的字典，如果出现使用错误，
        # 会显示这些信息以向用户提供更多上下文信息。
        self.extra_info = extra_info if extra_info else {}

    def error(self, message: str) -> NoReturn:
        """将 argparse 错误消息转换为 UsageError。
        
        :param message: 错误消息
        :raises UsageError: 总是抛出 UsageError 异常
        """
        msg = f"{self.prog}: error: {message}"

        # 如果解析器有配置源提示，添加到错误消息中
        if hasattr(self._parser, "_config_source_hint"):
            msg = f"{msg} ({self._parser._config_source_hint})"

        # 抛出 UsageError 而不是 SystemExit
        raise UsageError(self.format_usage() + msg)

    # Type ignored because typeshed has a very complex type in the superclass.
    def parse_args(  # type: ignore
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        """解析参数，允许位置参数的分割处理。
        
        :param args: 要解析的参数序列
        :param namespace: 可选的命名空间对象
        :returns: 解析后的命名空间对象
        
        这个方法扩展了标准解析，特别处理了未知参数和位置参数。
        """
        parsed, unrecognized = self.parse_known_args(args, namespace)
        if unrecognized:
            # 处理未知参数
            for arg in unrecognized:
                if arg and arg[0] == "-":
                    # 如果未知参数以 '-' 开头，认为是错误
                    lines = [
                        "unrecognized arguments: {}".format(" ".join(unrecognized))
                    ]
                    # 添加额外信息到错误消息
                    for k, v in sorted(self.extra_info.items()):
                        lines.append(f"  {k}: {v}")
                    self.error("\n".join(lines))
            # 将未知参数作为位置参数添加
            getattr(parsed, FILE_OR_DIR).extend(unrecognized)
        return parsed


class DropShorterLongHelpFormatter(argparse.HelpFormatter):
    """Shorten help for long options that differ only in extra hyphens.

    - Collapse **long** options that are the same except for extra hyphens.
    - Shortcut if there are only two options and one of them is a short one.
    - Cache result on the action object as this is called at least 2 times.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Use more accurate terminal width.
        if "width" not in kwargs:
            kwargs["width"] = _pytest._io.get_terminal_width()
        super().__init__(*args, **kwargs)

    def _format_action_invocation(self, action: argparse.Action) -> str:
        orgstr = super()._format_action_invocation(action)
        if orgstr and orgstr[0] != "-":  # only optional arguments
            return orgstr
        res: str | None = getattr(action, "_formatted_action_invocation", None)
        if res:
            return res
        options = orgstr.split(", ")
        if len(options) == 2 and (len(options[0]) == 2 or len(options[1]) == 2):
            # a shortcut for '-h, --help' or '--abc', '-a'
            action._formatted_action_invocation = orgstr  # type: ignore
            return orgstr
        return_list = []
        short_long: dict[str, str] = {}
        for option in options:
            if len(option) == 2 or option[2] == " ":
                continue
            if not option.startswith("--"):
                raise ArgumentError(
                    f'long optional argument without "--": [{option}]', option
                )
            xxoption = option[2:]
            shortened = xxoption.replace("-", "")
            if shortened not in short_long or len(short_long[shortened]) < len(
                xxoption
            ):
                short_long[shortened] = xxoption
        # now short_long has been filled out to the longest with dashes
        # **and** we keep the right option ordering from add_argument
        for option in options:
            if len(option) == 2 or option[2] == " ":
                return_list.append(option)
            if option[2:] == short_long.get(option.replace("-", "")):
                return_list.append(option.replace(" ", "=", 1))
        formatted_action_invocation = ", ".join(return_list)
        action._formatted_action_invocation = formatted_action_invocation  # type: ignore
        return formatted_action_invocation

    def _split_lines(self, text, width):
        """Wrap lines after splitting on original newlines.

        This allows to have explicit line breaks in the help text.
        """
        import textwrap

        lines = []
        for line in text.splitlines():
            lines.extend(textwrap.wrap(line.strip(), width))
        return lines


class OverrideIniAction(argparse.Action):
    """自定义 argparse 动作，使 CLI 标志等同于覆盖配置选项，
    同时表现得像 `store_true`。

    这可以简化事情，因为代码只需要检查 ini 选项
    而不需要考虑 CLI 标志。
    
    这个类允许通过命令行标志来覆盖配置文件中的设置，
    使得命令行参数和配置文件选项能够统一处理。
    """

    def __init__(
        self,
        option_strings: Sequence[str],  # 选项字符串列表
        dest: str,  # 目标属性名
        nargs: int | str | None = None,  # 参数数量（固定为 0）
        *args,
        ini_option: str,  # 要覆盖的 ini 选项名
        ini_value: str,  # ini 选项的值
        **kwargs,
    ) -> None:
        """初始化覆盖 ini 选项的动作。
        
        :param option_strings: 选项字符串列表
        :param dest: 目标属性名
        :param nargs: 参数数量（固定为 0）
        :param ini_option: 要覆盖的 ini 选项名
        :param ini_value: ini 选项的值
        """
        super().__init__(option_strings, dest, 0, *args, **kwargs)
        self.ini_option = ini_option  # 要覆盖的 ini 选项
        self.ini_value = ini_value  # ini 选项的值

    def __call__(
        self,
        parser: argparse.ArgumentParser,  # 解析器对象
        namespace: argparse.Namespace,  # 命名空间对象
        *args,
        **kwargs,
    ) -> None:
        """执行动作：设置标志并添加到覆盖列表。
        
        :param parser: argparse 解析器
        :param namespace: 要修改的命名空间
        """
        # 设置目标属性为 True（像 store_true 一样）
        setattr(namespace, self.dest, True)
        
        # 获取当前的覆盖列表
        current_overrides = getattr(namespace, "override_ini", None)
        if current_overrides is None:
            current_overrides = []
        
        # 添加新的覆盖项
        current_overrides.append(f"{self.ini_option}={self.ini_value}")
        
        # 更新命名空间中的覆盖列表
        setattr(namespace, "override_ini", current_overrides)
