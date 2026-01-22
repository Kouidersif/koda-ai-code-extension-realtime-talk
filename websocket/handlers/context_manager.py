import asyncio
import json
import logging
import hashlib
from typing import Optional, Dict, Any
from prompt_tools import set_current_context

logger = logging.getLogger(__name__)


class ContextManager:
    """Manages editor context state and injection logic."""
    
    def __init__(self):
        # Pending context (latest received, may not be injected yet)
        self.pending_context: Optional[Dict[str, Any]] = None
        self.pending_context_hash: Optional[str] = None
        # Last injected context hash (to avoid re-injecting identical context)
        self.last_injected_context_hash: Optional[str] = None
        # Track if we've injected context for the CURRENT user speech turn
        self.user_speech_detected = False
        self.context_injected_this_speech = False
        
        # Debug counters
        self.context_received_count = 0
        self.context_injected_count = 0
    
    def update_context(self, payload: Dict[str, Any]) -> bool:
        """
        Update context state with new payload.
        Returns True if context was updated, False if unchanged.
        """
        self.context_received_count += 1
        
        payload_type = payload.get("type")
        payload_subtype = payload.get("subtype")
        
        # Handle selection context
        if payload_type == "context" and payload_subtype == "selection":
            data = payload.get("data", {})
            selection = data.get("selection", {})
            selection_text = selection.get("text", "")
            selection_preview = selection_text[:100].replace('\n', ' ')
            hash_content = json.dumps({
                "type": "selection",
                "fileName": data.get("fileName"),
                "selectionText": selection_text[:500],
            }, sort_keys=True)
            new_hash = hashlib.md5(hash_content.encode()).hexdigest()[:16]
            
            logger.info(f"📥 RECEIVED selection context: file={data.get('fileName', 'unknown')}, "
                      f"chars={len(selection_text)}, "
                      f"lines={selection.get('start', {}).get('line', 0)+1}-{selection.get('end', {}).get('line', 0)+1}, "
                      f"preview='{selection_preview}...', "
                      f"hash={new_hash}, received_count={self.context_received_count}")
            
            if new_hash != self.pending_context_hash:
                self.pending_context = payload
                self.pending_context_hash = new_hash
                # Update context in prompt tools
                set_current_context(selection=payload)
                logger.info(f"✓ Selection context UPDATED and PENDING injection (hash={new_hash})")
                return True
            else:
                logger.debug(f"Selection context unchanged (hash={new_hash}), keeping current pending")
                return False
        
        # Handle workspace tree context
        if payload_type == "context" and payload_subtype == "tree":
            data = payload.get("data", {})
            tree_content = json.dumps(data, sort_keys=True)[:2000]
            new_hash = hashlib.md5(tree_content.encode()).hexdigest()[:16]
            
            if new_hash != self.pending_context_hash:
                self.pending_context = payload
                self.pending_context_hash = new_hash
                # Update context in prompt tools
                set_current_context(tree=payload)
                logger.info(f"Workspace tree context updated: roots={len(data.get('roots', []))}, hash={new_hash}")
                return True
            return False
        
        # Handle legacy editor_context format
        if payload_type == "editor_context":
            data = payload.get("data", {})
            hash_content = json.dumps({
                "fileName": data.get("fileName"),
                "cursor": data.get("cursor"),
                "selection": data.get("selection", {}).get("text", "")[:500] if data.get("selection") else None,
                "snippet": data.get("snippet", {}).get("text", "")[:2000] if data.get("snippet") else None,
            }, sort_keys=True)
            new_hash = hashlib.md5(hash_content.encode()).hexdigest()[:16]
            
            if new_hash != self.pending_context_hash:
                self.pending_context = payload
                self.pending_context_hash = new_hash
                logger.info(f"Editor context updated: file={data.get('fileName', 'unknown')}, "
                          f"hash={new_hash}, received_count={self.context_received_count}")
                return True
            else:
                logger.debug(f"Context unchanged (hash={new_hash}), skipping update")
                return False
        
        return False
    
    async def inject_context_if_needed(self, text_input_queue: asyncio.Queue):
        """Inject context only if: pending exists, hash differs from last injected, and not already injected this speech"""
        logger.debug(f"🔍 inject_context_if_needed called - pending={bool(self.pending_context)}, "
                    f"injected_this_speech={self.context_injected_this_speech}, "
                    f"pending_hash={self.pending_context_hash}, last_injected={self.last_injected_context_hash}")
        
        if not self.pending_context:
            logger.warning("❌ NO pending context to inject!")
            return
        if self.context_injected_this_speech:
            logger.debug("⏭️  Already injected context this speech turn, skipping")
            return
        if self.pending_context_hash == self.last_injected_context_hash:
            logger.debug(f"⏭️  Context unchanged (hash={self.pending_context_hash}), skipping injection")
            return
        
        # Get context type/subtype for logging
        ctx_type = self.pending_context.get('subtype', self.pending_context.get('type'))
        
        context_text = self._format_editor_context_for_gemini(self.pending_context)
        if context_text:
            try:
                await text_input_queue.put(context_text)
                self.last_injected_context_hash = self.pending_context_hash
                self.context_injected_this_speech = True
                self.context_injected_count += 1
                logger.info(f"💉 INJECTED {ctx_type} context to Gemini (hash={self.pending_context_hash}, "
                          f"total_injections={self.context_injected_count}, text_len={len(context_text)} chars)")
            except Exception as e:
                logger.error(f"❌ Error injecting context: {e}")
    
    def on_user_speech_started(self):
        """Mark that user speech has started for this turn."""
        if not self.user_speech_detected:
            self.user_speech_detected = True
            logger.info(f"▶️  User speech STARTED")
            logger.info(f"📋 Context state: pending={bool(self.pending_context)}, "
                      f"pending_hash={self.pending_context_hash}, "
                      f"last_injected={self.last_injected_context_hash}, "
                      f"injected_this_turn={self.context_injected_this_speech}")
    
    def reset_for_new_turn(self):
        """Reset state for a new conversation turn."""
        self.user_speech_detected = False
        self.context_injected_this_speech = False
    
    def _format_editor_context_for_gemini(self, context: dict) -> str:
        """
        Format editor context payload into a text message for Gemini.
        Handles both legacy 'editor_context' and new 'context' (selection/tree) formats.
        """
        try:
            context_type = context.get("type")
            subtype = context.get("subtype")
            data = context.get("data", {})
            
            parts = []
            
            # NEW: Handle selection-only context
            if context_type == "context" and subtype == "selection":
                parts.append("[SELECTION CONTEXT - The user has selected this code and wants to discuss it]")
                
                file_name = data.get("fileName", "unknown")
                language = data.get("languageId", "unknown")
                parts.append(f"File: {file_name} ({language})")
                
                selection = data.get("selection", {})
                if selection:
                    start = selection.get("start", {})
                    end = selection.get("end", {})
                    parts.append(f"Selection: lines {start.get('line', 0) + 1}-{end.get('line', 0) + 1}")
                    
                    sel_text = selection.get("text", "")
                    if sel_text:
                        parts.append(f"\n--- SELECTED CODE ---\n{sel_text}\n--- END SELECTION ---")
                        logger.debug(f"📄 Formatted selection context: {len(sel_text)} chars from {file_name}")
                    else:
                        logger.warning("⚠️  Selection context has NO text!")
                else:
                    logger.warning("⚠️  Selection context missing selection data!")
                
                parts.append("[END SELECTION CONTEXT]")
                formatted = "\n".join(parts)
                logger.debug(f"📝 Formatted context message: {len(formatted)} chars")
                return formatted
            
            # NEW: Handle workspace tree context
            if context_type == "context" and subtype == "tree":
                parts.append("[WORKSPACE TREE - Directory structure of the user's project]")
                
                roots = data.get("roots", [])
                for root in roots:
                    name = root.get("name", "workspace")
                    tree = root.get("tree", "")
                    parts.append(f"\n--- {name} ---\n{tree}")
                
                parts.append("[END WORKSPACE TREE]")
                return "\n".join(parts)
            
            # LEGACY: Handle editor_context format
            parts.append("[EDITOR CONTEXT - Use this to answer questions about the user's current code]")
            
            # File info
            file_name = data.get("fileName", "unknown")
            language = data.get("languageId", "unknown")
            parts.append(f"File: {file_name} ({language})")
            
            # Cursor position
            cursor = data.get("cursor")
            if cursor:
                parts.append(f"Cursor at line {cursor.get('line', 0) + 1}, column {cursor.get('character', 0) + 1}")
            
            # Selection (if any)
            selection = data.get("selection")
            if selection and selection.get("text"):
                sel_text = selection["text"]
                if len(sel_text) > 500:
                    sel_text = sel_text[:500] + "... (truncated)"
                parts.append(f"\n--- SELECTED CODE ---\n{sel_text}\n--- END SELECTION ---")
            
            # Code snippet around cursor
            snippet = data.get("snippet")
            if snippet and snippet.get("text"):
                start_line = snippet.get("startLine", 1)
                end_line = snippet.get("endLine", 1)
                parts.append(f"\n--- CODE SNIPPET (lines {start_line}-{end_line}) ---\n{snippet['text']}\n--- END SNIPPET ---")
            
            # Git diff (if available)
            git_diff = data.get("gitDiff")
            if git_diff:
                if len(git_diff) > 1000:
                    git_diff = git_diff[:1000] + "... (truncated)"
                parts.append(f"\n--- GIT DIFF ---\n{git_diff}\n--- END DIFF ---")
            
            parts.append("[END EDITOR CONTEXT]")
            
            return "\n".join(parts)
            
        except Exception as e:
            logger.error(f"Error formatting editor context: {e}")
            return ""