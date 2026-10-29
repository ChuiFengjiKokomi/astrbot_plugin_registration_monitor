"""测试使用合成数据，并支持任意 GitHub checkout 目录名。"""

import sys
from pathlib import Path
from types import ModuleType

PACKAGE_NAME = "astrbot_plugin_registration_monitor"
package = ModuleType(PACKAGE_NAME)
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules.setdefault(PACKAGE_NAME, package)


def example_payload(count=132):
    return {
        "success": True,
        "data": [
            {
                "id": str(index),
                "nameZh": f"示例大学{index}",
                "nameEn": f"Example University {index}",
                "rank": index,
            }
            for index in range(1, count + 1)
        ],
        "timestamp": 1792932019000,
    }
