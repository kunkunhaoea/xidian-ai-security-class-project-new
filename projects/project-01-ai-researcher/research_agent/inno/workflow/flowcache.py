import json
import os
from research_agent.inno.util import single_select_menu
from research_agent.inno.core import MetaChain, MetaChainLogger
from typing import Union, Dict, List, Callable, Any
from research_agent.inno import Agent
from abc import ABC, abstractmethod
from torch import nn

# 缓存版本（优化 P0，2026-11-10）：写缓存时打版本戳，读取时版本不符即忽略重跑，
# 避免代码/提示词变更后静默复用过期结果。缺失版本视为 legacy（向后兼容旧缓存）。
CACHE_VERSION = 1

# 各 Agent 缓存复用前必须存在的关键 context 键（优化 P0）：缺失说明缓存不完整
# （典型：崩溃时写坏的缓存），忽略并重跑，避免下游 KeyError。Prepare 另有更严格的结构校验。
_REQUIRED_CONTEXT_KEYS = {
    "Judge Agent": ("suggestion_dict",),
    "Experiment Analysis Agent": ("experiment_report",),
}

class AgentModule:
    def __init__(self, agent: Agent, client: MetaChain, cache_path: str):
        self.agent = agent
        self.client = client
        self.cache_path = cache_path
    async def __call__(self, messages: List[Dict], context_variables: Dict, iter_times: int = None, *args, **kwargs):
        # messages = [{"role": "user", "content": query}]
        agent_cache, escape_running = self.check_cache(self.agent.name, iter_times)
        if agent_cache and escape_running:
            messages.extend(agent_cache["messages"])
            context_variables.update(agent_cache["context_variables"])
        elif agent_cache and not escape_running:
            messages.extend(agent_cache["messages"])
            context_variables.update(agent_cache["context_variables"])
            response = await self.client.run_async(self.agent, messages, context_variables=context_variables, debug=True)
            ret_messages = response.messages
            ret_context_variables = response.context_variables
            # 优化 P0：失败不固化——末条为 error 时直接抛出、不写缓存，保留已有好缓存，
            # 避免坏结果被下次复用（曾导致下游 experiment_report KeyError）。
            if ret_messages[-1]["role"] == "error":
                raise Exception(ret_messages[-1]["content"])
            ret_messages.append({"role": "success", "content": "The agent successfully generated a response."})
            self.save_cache(self.agent.name, agent_cache["messages"] + ret_messages[:-1], iter_times, ret_context_variables)
            messages.extend(ret_messages[:-1])
            context_variables.update(ret_context_variables)
        else:
            response = await self.client.run_async(self.agent, messages, context_variables=context_variables, debug=True)
            ret_messages = response.messages
            ret_context_variables = response.context_variables
            # 优化 P0：失败不固化（同上）。
            if ret_messages[-1]["role"] == "error":
                raise Exception(ret_messages[-1]["content"])
            ret_messages.append({"role": "success", "content": "The agent successfully generated a response."})
            self.save_cache(self.agent.name, ret_messages[:-1], iter_times, ret_context_variables)
            messages.extend(ret_messages[:-1])
            context_variables.update(ret_context_variables)
        return messages, context_variables
    def save_cache(self, agent_name, messages, iter_times: int = None, context_variables: Dict = None):
        agent_name = agent_name.replace(" ", "_").lower()
        if iter_times is not None:
            agent_name = agent_name + f"_iter_{iter_times}"
        agent_cache_file = f"{self.cache_path}/agents/{agent_name}.json"
        os.makedirs(os.path.dirname(agent_cache_file), exist_ok=True)
        with open(agent_cache_file, "w", encoding="utf-8") as f:
            json.dump({"version": CACHE_VERSION, "messages": messages, "context_variables": context_variables}, f, ensure_ascii=False, indent=4)
    def check_cache(self, agent_name, iter_times: int = None):
        agent_name_norm = agent_name.replace(" ", "_").lower()
        if iter_times is not None:
            agent_name_norm = agent_name_norm + f"_iter_{iter_times}"
        cache_file = f"{self.cache_path}/agents/{agent_name_norm}.json"
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            # 优化 P0：版本校验——显式版本不符即忽略重跑；缺失版本按 legacy 接受（向后兼容）。
            if cached.get("version", CACHE_VERSION) != CACHE_VERSION:
                print(f"Ignoring '{agent_name}' cache: version mismatch (got {cached.get('version')}, want {CACHE_VERSION}); running again.")
                return None, False
            # 优化 P0：完整性校验——关键 context 键缺失视为坏缓存，忽略重跑。
            if agent_name == "Prepare Agent":
                result = (cached.get("context_variables") or {}).get("prepare_result")
                required = ("reference_codebases", "reference_paths", "reference_papers")
                if not isinstance(result, dict) or not all(
                    isinstance(result.get(key), list) for key in required
                ):
                    print("Ignoring incomplete Prepare Agent cache; running this stage again.")
                    return None, False
            for key in _REQUIRED_CONTEXT_KEYS.get(agent_name, ()):
                if key not in (cached.get("context_variables") or {}):
                    print(f"Ignoring incomplete '{agent_name}' cache (missing context key '{key}'); running again.")
                    return None, False
            choice = single_select_menu(["Yes", "Resume", "No"], f"The agent '{agent_name}' cache file exists, do you want to use it?")
            if choice == "Yes":
                return cached, True
            elif choice == "Resume":
                return cached, False
            else:
                return None, False
        return None, False
    
class ToolModule:
    def __init__(self, tool: Callable[[Any], Union[str, Dict]], cache_path: str):
        self.tool = tool
        self.cache_path = cache_path
    def __call__(self, tool_args: Dict, *args, **kwargs):
        tool_cache = self.check_cache(self.tool.__name__)
        if tool_cache:
            return tool_cache
        else:
            tool_result = self.tool(**tool_args)
            self.save_cache(self.tool, tool_args, tool_result)
            return tool_result
    def save_cache(self, tool: Callable, tool_args: Dict, tool_result: Union[str, Dict]):
        tool_name = tool.__name__

        tool_cache_file = f"{self.cache_path}/tools/{tool_name}.json"
        os.makedirs(os.path.dirname(tool_cache_file), exist_ok=True)
        tool_cache_dict = {
            "name": tool_name,
            "args": tool_args,
            "result": tool_result
        }
        with open(tool_cache_file, "w", encoding="utf-8") as f:
            json.dump(tool_cache_dict, f, ensure_ascii=False, indent=4)
    def check_cache(self, tool_name: str):
        tool_name = tool_name
        cache_file = f"{self.cache_path}/tools/{tool_name}.json"
        if os.path.exists(cache_file):
            choice = single_select_menu(["Yes", "No"], f"The tool '{tool_name}' cache file exists, do you want to use it?")
            if choice == "Yes":
                with open(cache_file, "r", encoding="utf-8") as f:
                    tool_cache_dict = json.load(f)
                    return tool_cache_dict["result"]
            else:
                return None
        return None

class FlowModule(ABC):
    def __init__(self, cache_path: str, log_path: Union[str, None, MetaChainLogger] = None, model: str = "gpt-4o-2024-08-06"):
        self.cache_path = cache_path
        self.client = MetaChain(log_path=log_path)
        self.model = model
    @abstractmethod
    async def forward(self, *args, **kwargs):
        raise NotImplementedError("subclass should implement this method")
    
    async def __call__(self, *args, **kwargs):
        return await self.forward(*args, **kwargs)

    
