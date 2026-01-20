"""
Simple Prompt Generator for GitHub Copilot Chat.

Gemini calls generate_prompt() when user needs coding help.
Returns a simple text prompt that gets sent to Copilot Chat.
"""

from google.genai import types
from typing import List, Dict, Any, Callable
import logging

logger = logging.getLogger(__name__)


def generate_prompt(
    task_description: str,
    context: str = "",
    code_snippet: str = "",
) -> Dict[str, Any]:
    """
    Generate a simple prompt for GitHub Copilot Chat.
    
    Args:
        task_description: What the user wants to do (AI generates this)
        context: File name, language, relevant info (optional)
        code_snippet: Code to reference (optional)
    
    Returns:
        {"success": True, "prompt": "Full prompt text ready for Copilot"}
    """
    logger.info(f"Generating prompt: {task_description[:50]}...")
    
    lines = [task_description]
    
    if context:
        lines.append(f"\nContext: {context}")
    
    if code_snippet:
        # Truncate if too long
        snippet = code_snippet[:1000] if len(code_snippet) > 1000 else code_snippet
        lines.append(f"\n```\n{snippet}\n```")
    
    prompt_text = "\n".join(lines)
    
    logger.info(f"Prompt generated: {len(prompt_text)} chars")
    
    return {
        "success": True,
        "prompt": prompt_text
    }


# Global context storage - set by main.py when context arrives
_current_context = {
    "selection": None,
    "tree": None,
}


def set_current_context(selection=None, tree=None, **kwargs):
    """Update the current context (called by main.py when context changes)."""
    global _current_context
    if selection is not None:
        _current_context["selection"] = selection
    if tree is not None:
        _current_context["tree"] = tree


def get_editor_context(include_selection: bool = True, include_tree: bool = True) -> Dict[str, Any]:
    """
    Get the current editor context.
    Called by Gemini to see what code the user is working with.
    """
    logger.info("get_editor_context called")
    
    result = {
        "success": True,
        "has_selection": False,
        "has_tree": False,
        "selection": None,
        "tree": None,
        "message": ""
    }
    
    messages = []
    
    sel = _current_context.get("selection")
    if include_selection and isinstance(sel, dict):
        sel_data = sel.get("data", {})
        selection_info = {
            "fileName": sel_data.get("fileName", "unknown"),
            "languageId": sel_data.get("languageId", "unknown"),
            "text": sel_data.get("selection", {}).get("text", ""),
            "startLine": sel_data.get("selection", {}).get("start", {}).get("line", 0) + 1,
            "endLine": sel_data.get("selection", {}).get("end", {}).get("line", 0) + 1,
        }
        if selection_info["text"]:
            result["has_selection"] = True
            result["selection"] = selection_info
            messages.append(f"Selected code from {selection_info['fileName']}")
    
    tree = _current_context.get("tree")
    if include_tree and isinstance(tree, dict):
        tree_data = tree.get("data", {})
        roots = tree_data.get("roots", [])
        if isinstance(roots, list) and roots:
            tree_list: List[Dict[str, Any]] = []
            for r in roots:  # type: ignore[misc]
                name = r.get("name") if isinstance(r, dict) else "workspace"
                structure = r.get("tree", "")[:2000] if isinstance(r, dict) else ""
                tree_list.append({"name": name, "structure": structure})
            if tree_list:
                result["has_tree"] = True
                result["tree"] = tree_list
                messages.append("Workspace tree")
    
    result["message"] = ", ".join(messages) if messages else "No context available"
    
    logger.info(f"get_editor_context: has_selection={result['has_selection']}, has_tree={result['has_tree']}")
    return result


def get_prompt_tools() -> List[types.Tool]:
    """
    Get the Gemini function tool definitions.
    """
    
    generate_prompt_declaration = types.FunctionDeclaration(
        name="generate_prompt",
        description="""Generate a prompt for GitHub Copilot Chat when user needs coding help.

Call this when user:
- Wants to implement something ("add error handling", "create a function")
- Has a bug or error ("this is broken", "getting an error")
- Wants to improve code ("make this better", "refactor this")
- Needs tests, docs, or review
- Is stuck or confused

Generate a clear, actionable prompt based on what the user needs.""",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "task_description": types.Schema(
                    type=types.Type.STRING,
                    description="Clear description of what the user wants. Be specific and actionable. This is the main prompt text."
                ),
                "context": types.Schema(
                    type=types.Type.STRING,
                    description="Optional: file name, language, or relevant context"
                ),
                "code_snippet": types.Schema(
                    type=types.Type.STRING,
                    description="Optional: relevant code to include (will be truncated if >1000 chars)"
                ),
            },
            required=["task_description"]
        )
    )
    
    get_editor_context_declaration = types.FunctionDeclaration(
        name="get_editor_context",
        description="""Get current editor context (selected code, workspace structure).
        
Call this when:
- User mentions "this code", "selected code", "current file"
- You need to see what the user is working on
- Before generating a prompt to get more context""",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "include_selection": types.Schema(
                    type=types.Type.BOOLEAN,
                    description="Include selected code (default: true)"
                ),
                "include_tree": types.Schema(
                    type=types.Type.BOOLEAN,
                    description="Include workspace tree (default: true)"
                ),
            },
            required=[]
        )
    )
    
    return [types.Tool(function_declarations=[
        generate_prompt_declaration,
        get_editor_context_declaration
    ])]


def get_prompt_tool_mapping() -> Dict[str, Callable[..., Any]]:
    """Get mapping of function names to implementations."""
    return {
        "generate_prompt": generate_prompt,
        "get_editor_context": get_editor_context
    }
