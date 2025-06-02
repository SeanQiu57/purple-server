import os
import sys
import importlib
import traceback
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.insert(0, project_root)
from config.logger import setup_logging

logger = setup_logging()


def create_instance(class_name, *args, **kwargs):
    # 1. 先构造插件文件路径，并打印尝试加载的日志
    base_dir = os.path.join("core", "providers", "memory", class_name)
    file_py = os.path.join(base_dir, f"{class_name}.py")
    logger.info(f"[MemoryLoader] 尝试加载记忆插件 `{class_name}`，路径={file_py}")

    # 2. 检查文件是否存在
    if not os.path.exists(file_py):
        logger.error(f"[MemoryLoader] 插件文件不存在：{file_py}")
        raise ValueError(f"不支持的记忆服务类型: {class_name}")

    # 3. 动态 import 模块
    lib_name = f"core.providers.memory.{class_name}.{class_name}"
    try:
        if lib_name not in sys.modules:
            module = importlib.import_module(lib_name)
            logger.info(f"[MemoryLoader] 模块 import 成功: {lib_name}")
        else:
            module = sys.modules[lib_name]
            logger.info(f"[MemoryLoader] 模块已缓存，直接使用: {lib_name}")
    except Exception:
        logger.exception(f"[MemoryLoader] import 模块 `{lib_name}` 失败")
        raise

    # 4. 实例化 MemoryProvider
    try:
        provider = module.MemoryProvider(*args, **kwargs)
        logger.info(f"[MemoryLoader] 实例化 MemoryProvider 成功: {class_name}")
        return provider
    except Exception:
        # 打印完整的异常堆栈
        logger.exception(f"[MemoryLoader] 实例化 `{class_name}` 失败.{traceback.format_exc()}")
        raise
