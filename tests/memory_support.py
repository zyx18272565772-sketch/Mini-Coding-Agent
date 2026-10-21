"""为其它模块的测试隔离记忆请求；真实记忆流程由 test_memory_* 覆盖。"""

from unittest.mock import Mock, patch


def isolate_memory(test_case, module):
    memory = Mock()
    memory.load_memories.return_value = ""
    memory.extract_memories.return_value = 0
    patcher = patch.object(module, "MemoryManager", return_value=memory)
    patcher.start()
    test_case.addCleanup(patcher.stop)
    return memory
