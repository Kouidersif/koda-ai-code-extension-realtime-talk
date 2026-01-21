/**
 * Copilot Chat Bridge - Sends prompts to GitHub Copilot Chat.
 */

import * as vscode from 'vscode';

/**
 * Options for opening Copilot Chat with a prompt.
 * In future, the 'autoSend' parameter will come from the backend tool call.
 */
export interface CopilotChatOptions {
    prompt: string;
    autoSend?: boolean;  // Future: Backend AI will decide based on context
}

/**
 * Open Copilot Chat with prompt pre-filled (user must submit manually).
 * This is the default behavior - gives user final control over submission.
 */
export async function openCopilotChatWithPrompt(options: CopilotChatOptions): Promise<boolean> {
    try {
        const { prompt, autoSend = false } = options;
        
        // Open chat with prompt pre-filled but NOT auto-submitted (isPartialQuery: true)
        // User sees the prompt and must press Enter to send
        await vscode.commands.executeCommand(
            'workbench.action.chat.open',
            { query: prompt, isPartialQuery: true }  // isPartialQuery=true = user must submit
        );
        
        console.log('[CopilotChat] Chat opened with prompt (awaiting user submission)');
        vscode.window.showInformationMessage('Prompt ready in Copilot Chat - press Enter to send', 'Review');
        
        return true;
    } catch (error) {
        console.error('[CopilotChat] Error opening chat:', error);
        handleCopilotError(error);
        return false;
    }
}

/**
 * Send a prompt directly to Copilot Chat with automatic submission.
 * This should only be called when backend explicitly requests auto-send via tool parameter.
 * 
 * @future This will be called when backend tool call includes autoSend: true
 */
export async function sendToCopilotChatAutoSubmit(prompt: string): Promise<boolean> {
    try {
        // isPartialQuery: false = automatically submits the prompt
        await vscode.commands.executeCommand(
            'workbench.action.chat.open',
            { query: prompt, isPartialQuery: false }
        );
        
        console.log('[CopilotChat] Prompt auto-submitted to Copilot Chat');
        return true;
    } catch (error) {
        console.error('[CopilotChat] Error auto-submitting prompt:', error);
        handleCopilotError(error);
        return false;
    }
}

/**
 * Handle common Copilot Chat errors
 */
async function handleCopilotError(error: any): Promise<void> {
    const copilotExtension = vscode.extensions.getExtension('GitHub.copilot-chat');
    if (!copilotExtension) {
        vscode.window.showErrorMessage(
            'GitHub Copilot Chat is not installed.',
            'Install'
        ).then(action => {
            if (action === 'Install') {
                vscode.commands.executeCommand(
                    'workbench.extensions.installExtension',
                    'GitHub.copilot-chat'
                );
            }
        });
    } else {
        vscode.window.showErrorMessage(`Failed to open Copilot Chat: ${error}`);
    }
}

/**
 * Legacy function for backwards compatibility - calls openCopilotChatWithPrompt
 * @deprecated Use openCopilotChatWithPrompt instead
 */
export async function sendToCopilotChat(prompt: string): Promise<boolean> {
    return openCopilotChatWithPrompt({ prompt, autoSend: false });
}
