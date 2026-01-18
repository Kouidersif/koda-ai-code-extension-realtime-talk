import * as vscode from 'vscode';
import * as crypto from 'crypto';
import * as path from 'path';
import { execSync } from 'child_process';

interface EditorContext {
    type: string;
    timestamp: number;
    data: any;
}

/**
 * Selection-only context payload.
 * Sends ONLY user-selected text, not snippets or full files.
 */
export interface SelectionContextPayload {
    type: 'context';
    subtype: 'selection';
    version: 1;
    timestamp: number;
    hash: string;
    data: {
        uri: string;
        fileName: string;
        languageId: string;
        selection: {
            start: { line: number; character: number };
            end: { line: number; character: number };
            text: string;
        };
    };
}

/**
 * Workspace tree context payload.
 * Sends directory structure (no file contents).
 */
export interface WorkspaceTreePayload {
    type: 'context';
    subtype: 'tree';
    version: 1;
    timestamp: number;
    hash: string;
    data: {
        roots: {
            name: string;
            uri: string;
            tree: string;
        }[];
    };
}

// Legacy type for backwards compatibility
export interface EditorContextPayload {
    type: 'editor_context';
    version: 1;
    timestamp: number;
    revision: number;
    hash?: string;
    data: {
        uri: string;
        fileName: string;
        languageId: string;
        lineCount: number;
        cursor: { line: number; character: number } | null;
        selection: {
            start: { line: number; character: number };
            end: { line: number; character: number };
            text: string;
        } | null;
        snippet: {
            startLine: number;
            endLine: number;
            text: string;
        } | null;
        fullText: string | null;
        gitDiff: string | null;
    };
}

export class EditorMonitor {
    private disposables: vscode.Disposable[] = [];
    private onContextChange: ((context: EditorContext) => void) | null = null;
    private onSelectionContext: ((context: SelectionContextPayload) => void) | null = null;
    private onTreeContext: ((context: WorkspaceTreePayload) => void) | null = null;
    
    // Legacy callback (for backwards compatibility)
    private onEditorContextChange: ((context: EditorContextPayload) => void) | null = null;
    
    private debounceTimers: Map<string, NodeJS.Timeout> = new Map();
    private lastFileChange: Map<string, string> = new Map();
    
    // Hash-based deduplication
    private lastSentSelectionHash: string = '';
    private lastSentTreeHash: string = '';
    
    // Debug counters
    private selectionSendCount: number = 0;
    private treeSendCount: number = 0;

    /**
     * Get configuration for context sharing
     */
    private getConfig() {
        const config = vscode.workspace.getConfiguration('aiNarrator.context');
        return {
            enabled: config.get<boolean>('enabled', true),
            selectionOnly: config.get<boolean>('selectionOnly', true),  // NEW: default true
            sendOnSelectionChange: config.get<boolean>('sendOnSelectionChange', false),  // NEW: default false (privacy)
            maxSelectionChars: config.get<number>('maxSelectionChars', 8000),
            includeWorkspaceTree: config.get<boolean>('includeWorkspaceTree', true),
            workspaceTreeDepth: config.get<number>('workspaceTreeDepth', 4),
            workspaceTreeMaxChars: config.get<number>('workspaceTreeMaxChars', 10000),
            workspaceTreeExcludeGlobs: config.get<string[]>('workspaceTreeExcludeGlobs', [
                'node_modules', '.git', 'dist', 'build', 'out', '.next', '.venv', '__pycache__', 
                '.cache', 'coverage', '.nyc_output', '.parcel-cache', '.turbo'
            ]),
        };
    }

    start(
        onContextChange: (context: EditorContext) => void, 
        onEditorContextChange?: (context: EditorContextPayload) => void,
        callbacks?: {
            onSelectionContext?: (context: SelectionContextPayload) => void;
            onTreeContext?: (context: WorkspaceTreePayload) => void;
        }
    ): void {
        this.onContextChange = onContextChange;
        this.onEditorContextChange = onEditorContextChange || null;
        this.onSelectionContext = callbacks?.onSelectionContext || null;
        this.onTreeContext = callbacks?.onTreeContext || null;
        // NEW: Selection-only mode listeners
        const config = this.getConfig();
        
        // Send workspace tree once at start (if enabled)
        if (config.includeWorkspaceTree && this.onTreeContext) {
            // Delay slightly to allow UI to initialize
            setTimeout(() => this.sendWorkspaceTreeNow(), 500);
        }
        
        // Selection change listener - only if sendOnSelectionChange is enabled
        if (config.sendOnSelectionChange) {
            this.disposables.push(
                vscode.window.onDidChangeTextEditorSelection((event) => {
                    if (event.textEditor === vscode.window.activeTextEditor) {
                        const hasSelection = !event.selections[0].isEmpty;
                        if (hasSelection) {
                            this.sendSelectionContextDebounced();
                        }
                    }
                })
            );
        }

        // Monitor file changes (for legacy context, if still needed)
        this.disposables.push(
            vscode.workspace.onDidChangeTextDocument((event) => {
                this.handleDocumentChange(event);
            })
        );

        // Monitor file saves
        this.disposables.push(
            vscode.workspace.onDidSaveTextDocument((document) => {
                this.handleFileSave(document);
            })
        );

        // Monitor terminal state (placeholder)
        this.disposables.push(
            vscode.window.onDidChangeTerminalState((terminal) => {
                // Terminal state changed
            })
        );

        console.log('[EditorMonitor] Started with config:', {
            selectionOnly: config.selectionOnly,
            sendOnSelectionChange: config.sendOnSelectionChange,
            includeWorkspaceTree: config.includeWorkspaceTree,
        });
    }

    // ==================== NEW: Selection-Only Context ====================

    private selectionDebounceTimer: NodeJS.Timeout | null = null;

    /**
     * Debounced sending of selection context
     */
    private sendSelectionContextDebounced(): void {
        const config = this.getConfig();
        if (!config.enabled || !this.onSelectionContext) {
            return;
        }

        if (this.selectionDebounceTimer) {
            clearTimeout(this.selectionDebounceTimer);
        }

        this.selectionDebounceTimer = setTimeout(() => {
            this.sendSelectionContextNow();
        }, 300); // 300ms debounce for selection
    }

    /**
     * Build selection-only context payload
     */
    public buildSelectionContextPayload(): SelectionContextPayload | null {
        const config = this.getConfig();
        const editor = vscode.window.activeTextEditor;
        
        if (!editor || editor.selection.isEmpty) {
            return null;
        }

        const document = editor.document;
        
        // Skip non-file schemes
        if (document.uri.scheme !== 'file' && document.uri.scheme !== 'untitled') {
            return null;
        }

        let selectionText = document.getText(editor.selection);
        
        // Truncate if too large
        if (selectionText.length > config.maxSelectionChars) {
            selectionText = selectionText.substring(0, config.maxSelectionChars) + '\n... (truncated)';
        }

        // Redact secrets
        selectionText = this.redactSecrets(selectionText);

        const payload: SelectionContextPayload = {
            type: 'context',
            subtype: 'selection',
            version: 1,
            timestamp: Date.now(),
            hash: '', // Will be filled below
            data: {
                uri: document.uri.toString(),
                fileName: this.getRelativeFileName(document.fileName),
                languageId: document.languageId,
                selection: {
                    start: { line: editor.selection.start.line, character: editor.selection.start.character },
                    end: { line: editor.selection.end.line, character: editor.selection.end.character },
                    text: selectionText
                }
            }
        };

        // Compute hash for deduplication
        payload.hash = this.computePayloadHash(payload);

        return payload;
    }

    /**
     * Immediately send current selection context
     */
    public sendSelectionContextNow(force: boolean = false): void {
        const config = this.getConfig();
        if (!config.enabled) {
            return;
        }

        const payload = this.buildSelectionContextPayload();
        if (!payload) {
            console.log('[EditorMonitor] No selection to send');
            return;
        }

        // Hash-based deduplication
        if (!force && payload.hash === this.lastSentSelectionHash) {
            console.log('[EditorMonitor] Selection unchanged, skipping send');
            return;
        }

        if (this.onSelectionContext) {
            this.onSelectionContext(payload);
        }

        // Also send via legacy callback if available (for backwards compatibility)
        if (this.onEditorContextChange) {
            const legacyPayload = this.convertToLegacyPayload(payload);
            if (legacyPayload) {
                this.onEditorContextChange(legacyPayload);
            }
        }

        this.lastSentSelectionHash = payload.hash;
        this.selectionSendCount++;
        
        console.log(`[EditorMonitor] Sent selection context (hash=${payload.hash.substring(0,8)}, count=${this.selectionSendCount})`);
    }

    /**
     * Get current file snippet (for manual send without selection)
     */
    public getCurrentFileSnippetPayload(): SelectionContextPayload | null {
        const config = this.getConfig();
        const editor = vscode.window.activeTextEditor;
        
        if (!editor) {
            return null;
        }

        const document = editor.document;
        
        // Skip non-file schemes
        if (document.uri.scheme !== 'file' && document.uri.scheme !== 'untitled') {
            return null;
        }

        // Get 50 lines around cursor (or all lines if file is small)
        const cursorLine = editor.selection.active.line;
        const snippetLines = 50;
        const halfLines = Math.floor(snippetLines / 2);
        let startLine = Math.max(0, cursorLine - halfLines);
        let endLine = Math.min(document.lineCount - 1, cursorLine + halfLines);

        if (startLine === 0) {
            endLine = Math.min(document.lineCount - 1, snippetLines - 1);
        }
        if (endLine === document.lineCount - 1) {
            startLine = Math.max(0, document.lineCount - snippetLines);
        }

        const range = new vscode.Range(startLine, 0, endLine, document.lineAt(endLine).text.length);
        let snippetText = document.getText(range);
        
        // Truncate if too large
        if (snippetText.length > config.maxSelectionChars) {
            snippetText = snippetText.substring(0, config.maxSelectionChars) + '\n... (truncated)';
        }

        // Redact secrets
        snippetText = this.redactSecrets(snippetText);

        const payload: SelectionContextPayload = {
            type: 'context',
            subtype: 'selection',
            version: 1,
            timestamp: Date.now(),
            hash: '',
            data: {
                uri: document.uri.toString(),
                fileName: this.getRelativeFileName(document.fileName),
                languageId: document.languageId,
                selection: {
                    start: { line: startLine, character: 0 },
                    end: { line: endLine, character: document.lineAt(endLine).text.length },
                    text: snippetText
                }
            }
        };

        payload.hash = this.computePayloadHash(payload);

        return payload;
    }

    // ==================== NEW: Workspace Tree Context ====================

    /**
     * Build workspace directory tree payload
     */
    public buildWorkspaceTreePayload(): WorkspaceTreePayload | null {
        const config = this.getConfig();
        const workspaceFolders = vscode.workspace.workspaceFolders;
        
        if (!workspaceFolders || workspaceFolders.length === 0) {
            return null;
        }

        const roots: WorkspaceTreePayload['data']['roots'] = [];

        for (const folder of workspaceFolders) {
            const tree = this.buildDirectoryTree(
                folder.uri.fsPath,
                config.workspaceTreeDepth,
                config.workspaceTreeExcludeGlobs
            );
            
            // Truncate tree if too large
            let treeStr = tree;
            if (treeStr.length > config.workspaceTreeMaxChars) {
                const lines = treeStr.split('\n');
                treeStr = '';
                for (const line of lines) {
                    if (treeStr.length + line.length > config.workspaceTreeMaxChars - 30) {
                        treeStr += '... (truncated)\n';
                        break;
                    }
                    treeStr += line + '\n';
                }
            }

            roots.push({
                name: folder.name,
                uri: folder.uri.toString(),
                tree: treeStr
            });
        }

        const payload: WorkspaceTreePayload = {
            type: 'context',
            subtype: 'tree',
            version: 1,
            timestamp: Date.now(),
            hash: '', // Will be filled below
            data: { roots }
        };

        payload.hash = this.computePayloadHash(payload);

        return payload;
    }

    /**
     * Build directory tree string (Unix tree command style)
     */
    private buildDirectoryTree(rootPath: string, maxDepth: number, excludeGlobs: string[]): string {
        const fs = require('fs');
        const pathModule = require('path');

        const lines: string[] = [];
        const rootName = pathModule.basename(rootPath);
        lines.push(rootName + '/');

        const walk = (dir: string, prefix: string, depth: number): void => {
            if (depth > maxDepth) return;

            let entries: string[] = [];
            try {
                entries = fs.readdirSync(dir);
            } catch (e) {
                return; // Permission denied or other error
            }

            // Filter out excluded directories/files
            entries = entries.filter((entry: string) => {
                return !excludeGlobs.some(glob => {
                    // Simple glob matching (just exact name match for now)
                    return entry === glob || entry.startsWith(glob + '/');
                });
            });

            // Sort: directories first, then files
            entries.sort((a: string, b: string) => {
                const aPath = pathModule.join(dir, a);
                const bPath = pathModule.join(dir, b);
                try {
                    const aIsDir = fs.statSync(aPath).isDirectory();
                    const bIsDir = fs.statSync(bPath).isDirectory();
                    if (aIsDir && !bIsDir) return -1;
                    if (!aIsDir && bIsDir) return 1;
                } catch (e) {
                    // Ignore stat errors
                }
                return a.localeCompare(b);
            });

            entries.forEach((entry: string, index: number) => {
                const entryPath = pathModule.join(dir, entry);
                const isLast = index === entries.length - 1;
                const connector = isLast ? '└── ' : '├── ';
                const newPrefix = prefix + (isLast ? '    ' : '│   ');

                let isDir = false;
                try {
                    isDir = fs.statSync(entryPath).isDirectory();
                } catch (e) {
                    // Skip entries we can't stat
                    return;
                }

                lines.push(prefix + connector + entry + (isDir ? '/' : ''));

                if (isDir) {
                    walk(entryPath, newPrefix, depth + 1);
                }
            });
        };

        walk(rootPath, '', 1);

        return lines.join('\n');
    }

    /**
     * Immediately send workspace tree context
     */
    public sendWorkspaceTreeNow(force: boolean = false): void {
        const config = this.getConfig();
        if (!config.enabled) {
            return;
        }

        const payload = this.buildWorkspaceTreePayload();
        if (!payload) {
            console.log('[EditorMonitor] No workspace tree to send');
            return;
        }

        // Hash-based deduplication
        if (!force && payload.hash === this.lastSentTreeHash) {
            console.log('[EditorMonitor] Workspace tree unchanged, skipping send');
            return;
        }

        if (this.onTreeContext) {
            this.onTreeContext(payload);
        }

        this.lastSentTreeHash = payload.hash;
        this.treeSendCount++;
        
        console.log(`[EditorMonitor] Sent workspace tree (hash=${payload.hash.substring(0,8)}, count=${this.treeSendCount})`);
    }

    // ==================== Hash Utilities ====================

    /**
     * Compute MD5 hash of payload for deduplication
     */
    private computePayloadHash(payload: SelectionContextPayload | WorkspaceTreePayload): string {
        const content = JSON.stringify(payload.data);
        return crypto.createHash('md5').update(content).digest('hex');
    }

    /**
     * Convert new selection payload to legacy format (for backwards compatibility)
     */
    private convertToLegacyPayload(selectionPayload: SelectionContextPayload): EditorContextPayload | null {
        const editor = vscode.window.activeTextEditor;
        if (!editor) return null;

        return {
            type: 'editor_context',
            version: 1,
            timestamp: selectionPayload.timestamp,
            revision: 0,
            hash: selectionPayload.hash,
            data: {
                uri: selectionPayload.data.uri,
                fileName: selectionPayload.data.fileName,
                languageId: selectionPayload.data.languageId,
                lineCount: editor.document.lineCount,
                cursor: { line: editor.selection.active.line, character: editor.selection.active.character },
                selection: selectionPayload.data.selection,
                snippet: null,  // No snippet in selection-only mode
                fullText: null,
                gitDiff: null
            }
        };
    }

    // ==================== Legacy Methods (document change handling) ====================

    private handleDocumentChange(event: vscode.TextDocumentChangeEvent): void {
        const fileName = event.document.fileName;
        
        // Debounce changes - only send after 500ms of no changes
        const existingTimer = this.debounceTimers.get(fileName);
        if (existingTimer) {
            clearTimeout(existingTimer);
        }

        const timer = setTimeout(() => {
            this.analyzeChanges(event.document, event.contentChanges);
            this.debounceTimers.delete(fileName);
        }, 500);

        this.debounceTimers.set(fileName, timer);
    }

    private analyzeChanges(document: vscode.TextDocument, changes: readonly vscode.TextDocumentContentChangeEvent[]): void {
        if (changes.length === 0) return;

        // Get git diff if available
        const diff = this.getGitDiff(document.fileName);

        this.sendContext({
            type: 'code_change',
            timestamp: Date.now(),
            data: {
                fileName: document.fileName,
                language: document.languageId,
                changeCount: changes.length,
                diff: diff,
                summary: this.summarizeChanges(changes)
            }
        });
    }

    private handleFileSave(document: vscode.TextDocument): void {
        const diff = this.getGitDiff(document.fileName);

        this.sendContext({
            type: 'file_save',
            timestamp: Date.now(),
            data: {
                fileName: document.fileName,
                language: document.languageId,
                lineCount: document.lineCount,
                diff: diff
            }
        });
    }

    private getGitDiff(filePath: string): string | null {
        try {
            const workspaceFolder = vscode.workspace.getWorkspaceFolder(vscode.Uri.file(filePath));
            if (!workspaceFolder) return null;

            const diff = execSync(
                `git diff HEAD "${filePath}"`,
                { 
                    cwd: workspaceFolder.uri.fsPath,
                    encoding: 'utf8',
                    timeout: 2000
                }
            );

            return diff.trim() || null;
        } catch (error) {
            return null;
        }
    }

    private summarizeChanges(changes: readonly vscode.TextDocumentContentChangeEvent[]): string {
        const totalAdded = changes.reduce((sum, change) => sum + change.text.length, 0);
        const totalRemoved = changes.reduce((sum, change) => sum + change.rangeLength, 0);

        if (totalAdded > totalRemoved) {
            return `Added ${totalAdded - totalRemoved} characters`;
        } else if (totalRemoved > totalAdded) {
            return `Removed ${totalRemoved - totalAdded} characters`;
        } else {
            return `Modified ${totalAdded} characters`;
        }
    }

    private sendContext(context: EditorContext): void {
        if (this.onContextChange) {
            this.onContextChange(context);
        }
    }

    // ==================== Cleanup ====================

    stop(): void {
        // Clear all timers
        this.debounceTimers.forEach(timer => clearTimeout(timer));
        this.debounceTimers.clear();
        
        if (this.selectionDebounceTimer) {
            clearTimeout(this.selectionDebounceTimer);
            this.selectionDebounceTimer = null;
        }

        // Dispose all event listeners
        this.disposables.forEach(d => d.dispose());
        this.disposables = [];

        this.onContextChange = null;
        this.onEditorContextChange = null;
        this.onSelectionContext = null;
        this.onTreeContext = null;

        console.log('[EditorMonitor] Stopped');
    }

    // ==================== Utility Methods ====================

    /**
     * Truncate text to max characters, preserving complete lines where possible
     */
    private truncateText(text: string, maxChars: number): string {
        if (text.length <= maxChars) {
            return text;
        }

        const truncated = text.substring(0, maxChars);
        const lastNewline = truncated.lastIndexOf('\n');
        
        if (lastNewline > maxChars * 0.8) {
            return truncated.substring(0, lastNewline) + '\n... (truncated)';
        }
        
        return truncated + '... (truncated)';
    }

    /**
     * Basic secret redaction
     */
    private redactSecrets(text: string): string {
        const patterns = [
            /(['"`])?(api[_-]?key|apikey|secret[_-]?key|auth[_-]?token|access[_-]?token|bearer)\1?\s*[:=]\s*(['"`])?[a-zA-Z0-9_\-]{20,}(['"`])?/gi,
            /AKIA[0-9A-Z]{16}/g,
            /-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----/g,
            /(?:password|pwd|passwd)\s*[:=]\s*['"`]?[^'"`\s;]{8,}['"`]?/gi,
        ];

        let redacted = text;
        for (const pattern of patterns) {
            redacted = redacted.replace(pattern, '[REDACTED]');
        }
        return redacted;
    }

    /**
     * Get relative file name from workspace
     */
    private getRelativeFileName(absolutePath: string): string {
        const workspaceFolders = vscode.workspace.workspaceFolders;
        if (workspaceFolders) {
            for (const folder of workspaceFolders) {
                if (absolutePath.startsWith(folder.uri.fsPath)) {
                    return absolutePath.substring(folder.uri.fsPath.length + 1);
                }
            }
        }
        return absolutePath.split(/[/\\]/).pop() || absolutePath;
    }

    // ==================== Public API ====================

    /**
     * Get current context (legacy)
     */
    getCurrentContext(): EditorContext | null {
        const editor = vscode.window.activeTextEditor;
        if (!editor) return null;

        return {
            type: 'current_state',
            timestamp: Date.now(),
            data: {
                fileName: editor.document.fileName,
                language: editor.document.languageId,
                lineCount: editor.document.lineCount,
                cursorPosition: editor.selection.active,
                selectedText: editor.document.getText(editor.selection)
            }
        };
    }

    /**
     * Get selection context payload
     */
    public getSelectionContextPayload(): SelectionContextPayload | null {
        return this.buildSelectionContextPayload();
    }

    /**
     * Get workspace tree payload
     */
    public getWorkspaceTreePayload(): WorkspaceTreePayload | null {
        return this.buildWorkspaceTreePayload();
    }
}