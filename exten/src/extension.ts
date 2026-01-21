import * as vscode from 'vscode';
import { GeminiLiveClient } from './geminiLiveClient';
import { AudioRecorder } from './audioRecorder';
import { EditorMonitor, EditorContextPayload, SelectionContextPayload, WorkspaceTreePayload } from './editorMonitor';
import { openCopilotChatWithPrompt } from './copilotChatBridge';

let geminiClient: GeminiLiveClient | undefined;
let audioRecorder: AudioRecorder | undefined;
let editorMonitor: EditorMonitor | undefined;
let statusBarItem: vscode.StatusBarItem;
let isActive = false;

export function activate(context: vscode.ExtensionContext) {
    console.log('AI Voice Narrator extension is now active');

    // Create status bar item 
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.text = "$(sparkle-filled)";
    statusBarItem.tooltip = "Zexo AI (Click to start)";
    statusBarItem.command = 'ai-narrator.toggle';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Initialize components
    geminiClient = new GeminiLiveClient();
    audioRecorder = new AudioRecorder();
    editorMonitor = new EditorMonitor();

    // Register commands
    const toggleCommand = vscode.commands.registerCommand('ai-narrator.toggle', async () => {
        if (isActive) {
            await stopNarration();
        } else {
            await startNarration();
        }
    });

    const startCommand = vscode.commands.registerCommand('ai-narrator.start', startNarration);
    const stopCommand = vscode.commands.registerCommand('ai-narrator.stop', stopNarration);
    const muteCommand = vscode.commands.registerCommand('ai-narrator.mute', toggleMute);
    const statusCommand = vscode.commands.registerCommand('ai-narrator.status', () => {
        if (audioRecorder) {
            const status = audioRecorder.getStatus();
            vscode.window.showInformationMessage(`Audio Status: ${status}`);
            console.log('Full audio status:', status);
        }
    });

    // Context sharing commands
    const sendContextCommand = vscode.commands.registerCommand('ai-narrator.sendContext', () => {
        if (isActive && editorMonitor && geminiClient) {
            // Send current file snippet (50 lines around cursor)
            const payload = editorMonitor.getCurrentFileSnippetPayload();
            if (payload) {
                geminiClient.sendSelectionContext(payload);
                vscode.window.showInformationMessage(`Current file context sent: ${payload.data.fileName}`);
            } else {
                vscode.window.showWarningMessage('No active editor');
            }
        } else {
            vscode.window.showWarningMessage('AI Narrator must be active to send context');
        }
    });

    // NEW: Send selection command
    const sendSelectionCommand = vscode.commands.registerCommand('ai-narrator.sendSelection', () => {
        if (isActive && editorMonitor && geminiClient) {
            const payload = editorMonitor.getSelectionContextPayload();
            if (payload) {
                geminiClient.sendSelectionContext(payload);
                vscode.window.showInformationMessage(`Selection sent: ${payload.data.fileName} (${payload.data.selection.text.length} chars)`);
            } else {
                vscode.window.showWarningMessage('No text selected. Select text first, or use "Send Current File Context".');
            }
        } else {
            vscode.window.showWarningMessage('AI Narrator must be active to send selection');
        }
    });

    // NEW: Send workspace tree command
    const sendTreeCommand = vscode.commands.registerCommand('ai-narrator.sendWorkspaceTree', () => {
        if (isActive && editorMonitor && geminiClient) {
            const payload = editorMonitor.getWorkspaceTreePayload();
            if (payload) {
                geminiClient.sendTreeContext(payload);
                vscode.window.showInformationMessage('Workspace tree sent to AI');
            } else {
                vscode.window.showWarningMessage('No workspace open');
            }
        } else {
            vscode.window.showWarningMessage('AI Narrator must be active to send tree');
        }
    });

    const toggleAutoSelectCommand = vscode.commands.registerCommand('ai-narrator.toggleAutoSelect', () => {
        const config = vscode.workspace.getConfiguration('aiNarrator.context');
        const currentValue = config.get<boolean>('sendOnSelectionChange', true);
        const newValue = !currentValue;
        config.update('sendOnSelectionChange', newValue, true);
        vscode.window.showInformationMessage(
            newValue 
                ? '✓ Auto-send selection enabled - Select text to send automatically' 
                : '✗ Auto-send disabled - Use CMD+Shift+S to send selection manually'
        );
    });

    context.subscriptions.push(
        toggleCommand, startCommand, stopCommand, muteCommand, 
        statusCommand, sendContextCommand, sendSelectionCommand, sendTreeCommand, 
        toggleAutoSelectCommand
    );

    // Set up event handlers
    setupEventHandlers();
}

async function startNarration() {
    if (isActive) return;

    try {
        // Get backend URL from settings
        const config = vscode.workspace.getConfiguration('aiNarrator');
        const backendUrl = config.get<string>('backendUrl', 'ws://localhost:8001/ws');

        // Connect to Gemini Live backend
        await geminiClient!.connect(backendUrl);

        // Start audio recording
        await audioRecorder!.start((audioData) => {
            geminiClient!.sendAudio(audioData);
        });

        // Enable passthrough mode initially (disable VAD for now)
        audioRecorder!.enablePassthrough();

        // Start monitoring editor with new selection-only callbacks
        editorMonitor!.start(
            (context) => {
                geminiClient!.sendContext(context);
            },
            (editorContext: EditorContextPayload) => {
                // Legacy callback - send editor context when available
                geminiClient!.sendEditorContext(editorContext);
            },
            {
                onSelectionContext: (selectionContext: SelectionContextPayload) => {
                    geminiClient!.sendSelectionContext(selectionContext);
                },
                onTreeContext: (treeContext: WorkspaceTreePayload) => {
                    geminiClient!.sendTreeContext(treeContext);
                }
            }
        );

        // Note: Workspace tree is sent automatically by EditorMonitor.start() if configured

        isActive = true;
        statusBarItem.text = "$(sparkle-filled)~";
        statusBarItem.tooltip = "Zexo AI is listening";
        statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');

        vscode.window.showInformationMessage('AI Narrator started - listening to your workflow');
    } catch (error) {
        vscode.window.showErrorMessage(`Failed to start AI Narrator: ${error}`);
        console.error('Start error:', error);
    }
}

async function stopNarration() {
    if (!isActive) return;

    try {
        // Stop audio recording first to prevent further data sending
        audioRecorder!.stop();
        
        // Stop editor monitoring
        editorMonitor!.stop();
        
        // Disconnect from backend
        await geminiClient!.disconnect();

        isActive = false;
        statusBarItem.text = "$(sparkle-filled)";
        statusBarItem.tooltip = "Zexo AI (Click to start)";
        statusBarItem.backgroundColor = undefined;
        statusBarItem.show();

        vscode.window.showInformationMessage('AI Narrator stopped');
    } catch (error) {
        vscode.window.showErrorMessage(`Failed to stop AI Narrator: ${error}`);
        console.error('Stop error:', error);
    }
}

function toggleMute() {
    audioRecorder!.toggleMute();
    const isMuted = audioRecorder!.isMuted();

    if (isMuted) {
        statusBarItem.text = "$(sparkle-filled) $(mute)";
        statusBarItem.tooltip = "Zexo AI (Muted)";
        vscode.window.showInformationMessage('AI Narrator muted');
    } else {
        statusBarItem.text = "$(sparkle-filled)~";
        statusBarItem.tooltip = "Zexo AI is listening";
        vscode.window.showInformationMessage('AI Narrator unmuted');
    }
}

function setupEventHandlers() {
    // Handle Gemini audio responses
    geminiClient!.on('audio', (audioData) => {
        try {
            if (!isActive) return;
            audioRecorder!.playAudio(audioData);
        } catch (error) {
            console.error('Error playing audio:', error);
        }
    });

    geminiClient!.on('transcription', (data) => {
        try {
            console.log('User said:', data.transcription);
            if (data.translation) {
                console.log('Translation:', data.translation);
            }
        } catch (error) {
            console.error('Error handling transcription:', error);
        }
    });

    geminiClient!.on('user_transcription', (text) => {
        try {
            console.log('User speaking detected:', text);
            // Note: If you want to interrupt AI when user speaks, uncomment:
            // if (isActive) audioRecorder!.interruptPlayback();
        } catch (error) {
            console.error('Error handling user transcription:', error);
        }
    });

    geminiClient!.on('gemini_response', (text) => {
        try {
            console.log('Gemini said:', text);
        } catch (error) {
            console.error('Error handling gemini response:', error);
        }
    });

    geminiClient!.on('turn_complete', () => {
        try {
            console.log('[Extension] Turn complete - signaling end of audio session');
            if (!isActive) return;
            
            // Signal that all audio chunks have arrived
            audioRecorder!.endCurrentSession();
            
            console.log('[Extension] Audio session ended, status:', audioRecorder!.getStatus());
        } catch (error) {
            console.error('Error handling turn_complete:', error);
        }
    });

    geminiClient!.on('error', (error) => {
        try {
            console.error('[Extension] Gemini error:', error);
            if (isActive) {
                vscode.window.showErrorMessage(`AI Narrator error: ${error}`);
            }
        } catch (err) {
            console.error('Error displaying error message:', err);
        }
    });

    geminiClient!.on('interrupted', () => {
        try {
            console.log('[Extension] Gemini interrupted - stopping playback');
            if (!isActive) return;
            audioRecorder!.interruptPlayback();
        } catch (error) {
            console.error('Error handling interrupted:', error);
        }
    });

    geminiClient!.on('disconnected', () => {
        try {
            console.log('[Extension] Gemini disconnected');
            if (isActive) {
                console.log('[Extension] Active session - stopping narration');
                stopNarration().catch(err => console.error('Error stopping narration:', err));
            }
        } catch (error) {
            console.error('Error handling disconnected:', error);
        }
    });

    // Handle generated prompts from Gemini - open in Copilot Chat for user review
    // Future: The backend will include autoSend parameter to control submission
    geminiClient!.on('prompt_ready', async (event: { prompt: string; autoSend?: boolean }) => {
        try {
            console.log('[Extension] Prompt generated, opening in Copilot Chat');
            console.log('[Extension] Prompt:', event.prompt);
            console.log('[Extension] Auto-send:', event.autoSend ?? false);
            
            const success = await openCopilotChatWithPrompt({ 
                prompt: event.prompt,
                autoSend: event.autoSend ?? false  // Default: user must submit
            });
            
            if (success) {
                const message = event.autoSend 
                    ? '✓ Prompt submitted to Copilot Chat'
                    : '✓ Prompt ready in Copilot Chat - review and press Enter to send';
                console.log('[Extension]', message);
            }
        } catch (error) {
            console.error('[Extension] Error handling prompt:', error);
            vscode.window.showErrorMessage(`Failed to open Copilot Chat: ${error}`);
        }
    });
}

export function deactivate() {
    if (isActive) {
        editorMonitor?.stop();
        audioRecorder?.stop();
        geminiClient?.disconnect();
    }
}