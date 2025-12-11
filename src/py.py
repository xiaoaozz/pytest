# pylib 模块移除的兼容性垫片文件
# 如果系统中安装了 pylib，此文件将被跳过
# 因为 `py/__init__.py` 具有更高的导入优先级
from __future__ import annotations

import sys

# 导入 pytest 内部实现的 error 和 path 模块
# 这些模块原本来自 pylib，现在已集成到 pytest 中
import _pytest._py.error as error
import _pytest._py.path as path


# 将导入的模块注册到 sys.modules 中
# 这样当其他代码尝试导入 py.error 和 py.path 时
# 实际上会使用 pytest 内部的实现，保持向后兼容性
sys.modules["py.error"] = error
sys.modules["py.path"] = path

# 定义模块的公共接口
# 当使用 `from py import error, path` 时，这些模块会被导入
__all__ = ["error", "path"]
