"""Optional source-checkout bridge; no changes to native workspace defaults."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import sys

SHARED_TOPICS = Path(__file__).resolve().parents[4] / "shared" / "topics"
SHARED_TOOL_NAMES = frozenset({"workspace_list", "workspace_read", "workspace_search", "workspace_present",
                               "workspace_save", "workspace_export", "ask_user", "web_search", "paper_search"})


@lru_cache(maxsize=1)
def shared_discussion():
    repository = Path(__file__).resolve().parents[4]
    if not (repository / "adapters" / "deeptutor" / "files.py").is_file():
        raise ValueError("共享适配层不存在；请从完整课程仓库运行项目03")
    if str(repository) not in sys.path:
        sys.path.insert(0, str(repository))
    from adapters.deeptutor.files import SharedDiscussion

    return SharedDiscussion()


def linked_session(session_id, workspace_id):
    """Native sessions outside a shared topic keep all original behavior."""
    if not workspace_id:
        return None
    from deeptutor.services.workspace import get_content_workspace_service

    service = get_content_workspace_service()
    binding = service.binding_by_id(workspace_id)
    if binding.root.parent.resolve() != SHARED_TOPICS.resolve():
        return None
    bridge = shared_discussion()
    topic = bridge.topic_for_root(binding.root)
    record = bridge.references(topic, session_id)
    return (bridge, topic, record) if record else None


def apply_shared_context(context):
    linked = linked_session(context.session_id, context.runtime.workspace.workspace_id)
    if not linked:
        return None
    bridge, topic, _ = linked
    block, snapshot = bridge.live_context(topic, context.session_id, context.runtime.turn_id)
    context.user_message = block + "\n\n[用户问题]\n" + context.user_message
    # Do not mix account-global memory, skills, KBs or historical notebooks
    # into a shared-topic conversation merely because they exist in 03.
    context.memory_context = ""
    context.persona_context = ""
    context.skills_manifest = ""
    context.knowledge_bases = []
    for key in ("memory_context", "book_context", "question_bank_context", "learner_profile_prompt"):
        context.metadata[key] = ""
    context.allowed_builtin_tools = ["ask_user"]
    context.enabled_tools = [t for t in context.enabled_tools or [] if t in {"web_search", "paper_search"}]
    context.metadata["workbench_topic"] = topic
    context.metadata["workbench_read"] = [{k: v for k, v in d.items() if k != "text"} for d in snapshot["documents"]]
    context.metadata["workbench_warnings"] = snapshot["warnings"]
    # workspace_* use the bound root; unrestricted exec/MCP/skills are absent
    # from this shared-session surface. Native independent chats are unchanged.
    return snapshot
